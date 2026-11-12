"""D-8 协议联调测试：容器归属与动了别人东西事件、稀罕战利品里程碑、use 对空未生效闭包、提示词口径。"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.minecraft.attention_collector import MaicraftAttentionCollector
from src.agents.minecraft.goal_ledger import finish_notice, goal_failure_line, is_use_on_air_misuse
from src.agents.minecraft.maicraft import goal_run_of
from src.agents.minecraft.others_things import OTHERS_THINGS_TOUCHED, OthersThingsLog, others_things_of, spoken_line
from src.agents.minecraft.tools import build_report_spec
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager

from .minecraft_collector_fakes import FakeEventBus as _CollectorBus
from .minecraft_collector_fakes import FakeMcpClient as _FakeClient
from .minecraft_collector_fakes import events_page as _page
from .minecraft_collector_fakes import fake_client_factory
from .minecraft_v1_fakes import FakeMaicraft, _call, _resp, _texts, _wait_until, make_minecraft_agent


def _others_touched_event(
    owner: str = "玩家甲",
    owner_uuid: str = "ae0dce67-1111-2222-3333-444455556666",
    cursor: int = 10,
) -> Dict[str, Any]:
    return {
        "cursor": cursor,
        "kind": OTHERS_THINGS_TOUCHED,
        "goal_id": 1414,
        "ability": "maicraft:obtain",
        "message": f"从 {owner} 放的箱子（-352, 65, 53）拿了 3 个铁锭、1 个面包",
        "others_things": {
            "action": "took",
            "owner": owner,
            "owner_uuid": owner_uuid,
            "block": "minecraft:chest",
            "at": {"x": -352, "y": 65, "z": 53, "dimension": "minecraft:overworld"},
            "items": [{"item": "minecraft:iron_ingot", "count": 3}, {"item": "minecraft:bread", "count": 1}],
        },
    }


async def test_attention_collector_forwards_others_things_touched_and_rare_loot_found() -> None:
    """采集器：动了别人东西发身体事件（去坐标、带玩家身份与认账口径）；开出稀罕战利品转 game.milestone。"""
    bus = _CollectorBus()
    collector = MaicraftAttentionCollector(
        config={},
        event_bus=bus,  # type: ignore[arg-type]
        client_factory=fake_client_factory(),
        clock=lambda: 1000,
    )
    assert await collector._ensure_ready() is True
    client = _FakeClient.instances[-1]
    client.pages.append(_page([], cursor=5))
    await collector._drain()

    rare_event = {
        "cursor": 6,
        "kind": "rare_loot_found",
        "goal_id": 1259,
        "ability": "maicraft:loot",
        "message": "打开战利品箱（-311, 59, 73）开出了海洋之心 ×1，已经拿走",
        "rare_loot": {
            "block": "lootr:lootr_chest",
            "at": {"x": -311, "y": 59, "z": 73, "dimension": "minecraft:overworld"},
            "items": [{"item": "minecraft:heart_of_the_sea", "count": 1, "name": "海洋之心", "taken": 1}],
        },
    }
    offline_event = _others_touched_event(owner="一位不在线的玩家", cursor=8)
    client.pages.append(_page([rare_event, _others_touched_event(cursor=7), offline_event], cursor=8))
    await collector._drain()

    assert len(bus.events) == 3
    m_name, m_payload = bus.events[0]
    assert m_name == CoreEvents.GAME_MILESTONE and isinstance(m_payload, GamePayload)
    assert m_payload.message == "打开战利品箱开出了海洋之心 ×1，已经拿走"

    b_name, b_payload = bus.events[1]
    assert b_name == CoreEvents.GAME_BODY_UNKNOWN and isinstance(b_payload, BodyEventPayload)
    assert b_payload.source_event_type == OTHERS_THINGS_TOUCHED
    assert b_payload.player_name == "玩家甲"
    assert b_payload.player_uuid == "ae0dce67-1111-2222-3333-444455556666"
    assert "-352" not in b_payload.summary and "玩家甲 问起就大方认下" in b_payload.summary

    # 不在线玩家占位词不当作真实玩家名挂上卡片
    _, off_payload = bus.events[2]
    assert isinstance(off_payload, BodyEventPayload)
    assert off_payload.player_name == "" and "主人问起就大方认下" in off_payload.summary


def test_others_things_log_isolates_by_world_and_builds_fallback_fact() -> None:
    log = OthersThingsLog(keep_per_world=2)
    for owner in ("玩家甲", "玩家乙", "玩家甲"):
        log.record("world-a", others_things_of(_others_touched_event(owner=owner), at_ms=1))
    log.record("world-b", others_things_of(_others_touched_event(owner="玩家乙"), at_ms=1))
    assert [item["owner"] for item in log.view("world-a")] == ["玩家乙", "玩家甲"]
    assert [item["owner"] for item in log.view("world-b")] == ["玩家乙"]
    assert log.view("world-c") == []

    fallback = spoken_line(
        others_things_of(
            {
                "kind": OTHERS_THINGS_TOUCHED,
                "message": "",
                "others_things": {"action": "broke", "owner": "玩家甲", "block": "minecraft:oak_planks", "count": 3},
            },
            at_ms=2,
        )
    )
    assert "拆掉了 玩家甲 放的" in fallback and "3 格" in fallback


async def test_agent_records_others_things_touched_even_when_idle_and_injects_into_next_task() -> None:
    """闲着时收到动了别人东西事件也入账，下一件委派一开工就附带 [动过别人放的东西] 供认账。"""
    server = FakeMaicraft()

    def script(_messages: List[Dict[str, Any]]) -> Response:
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过了"})])

    agent, llm, _ = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="看看周围", task_id="d-1")
    await _wait_until(lambda: not agent.batch_active and len(llm.seen) == 1)

    ev = _others_touched_event(owner="玩家甲")
    server.body_event(OTHERS_THINGS_TOUCHED, ev["message"], {"others_things": ev["others_things"]})
    await _wait_until(lambda: "others_things_touched" in agent._current_task_context())

    agent.receive_delegation(instruction="玩家甲问是不是你拿了他箱子里的铁锭", task_id="d-2")
    await _wait_until(lambda: len(llm.seen) == 2 and not agent.batch_active)
    opened = _texts(llm.seen[-1])
    assert "[动过别人放的东西]" in opened and "玩家甲" in opened and "minecraft:iron_ingot" in opened
    await agent.stop()


def test_use_on_air_misuse_is_diagnosed_as_wrong_usage_not_broken_item_or_account() -> None:
    """协议第 6 节（场 65 魂符三次对空失败）：status=failed + used_on=air + not_possible_here 明确判定是用法不对。"""
    air_fail = goal_run_of(
        {
            "goal_id": 12,
            "ability": "maicraft:use",
            "purpose": "放出女仆",
            "state": "finished",
            "result": {
                "status": "failed",
                "summary": "对着空气用了魂符，没有效果",
                "problem": {"kind": "NOT_POSSIBLE_HERE", "text": "对着空气用了魂符，没有效果"},
                "details": {"used_on": "air"},
            },
        }
    )
    assert air_fail is not None
    assert is_use_on_air_misuse(air_fail) is True
    notice = finish_notice(air_fail, "")
    assert "【宿主判定】" in notice
    assert 'target={"kind": "here"}' in notice and 'face="up"' in notice
    assert "不是这个账号或权限放不出来" in notice

    streamer_line = goal_failure_line(air_fail)
    assert "对着空气用没生效" in streamer_line and "不是东西用不了" in streamer_line

    # 真对着方块失败（used_on=block）不误判为对空误用
    block_fail = goal_run_of(
        {
            "goal_id": 13,
            "ability": "maicraft:use",
            "purpose": "开箱子",
            "state": "finished",
            "result": {
                "status": "failed",
                "summary": "箱子上方被方块压住打不开",
                "problem": {"kind": "NOT_POSSIBLE_HERE", "text": "上方被压住"},
                "details": {"used_on": "block"},
            },
        }
    )
    assert block_fail is not None
    assert is_use_on_air_misuse(block_fail) is False
    assert "【宿主判定】" not in finish_notice(block_fail, "")


def test_minecraft_prompt_and_report_spec_reflect_d4_ownership_and_loot_rules() -> None:
    """提示词与上报规格跟上 D4 第 3、4 条及协议第 6 节：归属是事实不拦、战利品顺路开、脚下放物品用 here+face=up。"""
    reset_prompt_manager()
    try:
        prompt = get_prompt_manager().render("amaidesu_minecraft_agent")
    finally:
        reset_prompt_manager()
    assert "归属是事实，动别人的东西先掂量、动了要说" in prompt
    assert "没开过的战利品容器顺路搜刮" in prompt
    assert "`loot`" in prompt
    assert 'target: {"kind": "here"}' in prompt and '"face": "up"' in prompt
    assert "others_things_touched" in prompt
    assert "被保护挡住（结果说那东西" not in prompt

    report_desc = build_report_spec().parameters_schema["properties"]["content"]["description"]
    assert "归属是事实说明" in report_desc
    assert "被保护挡住时" not in report_desc
