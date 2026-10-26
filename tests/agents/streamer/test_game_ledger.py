"""游戏事件账本单元测试：标签构造、折叠窗口、事实渲染与观察工具回执。

对照集取自旧提取器（``extract_state_facts``，已随反解析链路退役）的既有测试
夹具，事实块的期望文本与其输出逐字节一致（重构时对照锁定）。行形态与时间线
game 条目一致（``[HH:MM 标签] 正文``）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import pytest

from src.agents.streamer.game_ledger import GameLedger


def _ts(hour: int, minute: int) -> int:
    """本地当天 HH:MM → 毫秒时刻（note_turn 的钟点标记由此渲染）。"""
    return int(datetime(2026, 1, 1, hour, minute).timestamp() * 1000)


# 对照集：与旧提取器测试夹具同一批事件（乱序进入，事实块保持入账顺序）
_FIXTURES: List[Dict[str, Any]] = [
    {"game": "minecraft", "type": "milestone", "msg": "采集完成：8 个橡木原木进了背包", "ts": _ts(20, 1)},
    {"game": "minecraft", "type": "report", "msg": "本次目标交付：蜂蜜胶已做好并收进背包", "ts": _ts(20, 10)},
    {"game": "minecraft", "type": "attention_required", "msg": "附近有苦力怕在游荡", "ts": _ts(20, 5)},
    {"kind": "died", "game": "minecraft", "summary": "角色倒下了：被苦力怕炸死", "resolved": True, "ts": _ts(20, 7)},
    {"kind": "respawned", "game": "minecraft", "summary": "角色在床边重生", "resolved": False, "ts": _ts(20, 8)},
    {
        "kind": "attacked",
        "game": "minecraft",
        "summary": "身体挨了一阵打：骷髅射了两箭",
        "resolved": False,
        "ts": _ts(20, 3),
    },
]

_RECEIPT: Dict[str, Any] = {
    "body": {"dimension": "minecraft:overworld", "health": 20.0, "food": 18, "held": "铁镐"},
    "inventory": ["橡木原木 ×8", "床 ×1", "铁锭 ×3"],
    "work": {"todo": "做一个蜂蜜胶", "status": "running"},
    "entities": [{"type": "creeper", "distance": 12.0}],
}


def _stage_fixture(ledger: GameLedger) -> None:
    """把对照集事件按序喂给账本（只入账，不消费）。"""
    for item in _FIXTURES:
        if "kind" in item:
            ledger.record_encounter(item["game"], item["kind"], item["summary"], item["resolved"], item["ts"])
        else:
            ledger.record_event(item["game"], item["type"], item["msg"], item["ts"])
    ledger.record_chat("minecraft", "玩家 Steve：你们服务器几点天亮", _ts(20, 9))


def test_label_construction() -> None:
    """三类行标签由账本统一构造，形态固定。"""
    assert GameLedger.game_label("minecraft", "died") == "游戏·minecraft·died"
    assert GameLedger.encounter_label("minecraft", "attacked") == "遭遇·minecraft·attacked"
    assert GameLedger.delegation_label("minecraft") == "我在 minecraft 里开始做"
    assert GameLedger.delegation_label("") == "我在游戏里开始做"


def test_facts_golden_text() -> None:
    """行为不变量：事实块文本与旧提取器在该夹具上的输出逐字节一致（对照锁定）。

    期望文本为重构时旧 ``extract_state_facts`` 对同一批事件的实际输出：保持入账
    顺序（不按钟点排序），只收状态跃迁条目，钟点标记原样保留。
    """
    ledger = GameLedger()
    _stage_fixture(ledger)
    expected = "\n".join(
        [
            f"[{_ts_label(_ts(20, 1))} 游戏·minecraft·milestone] 采集完成：8 个橡木原木进了背包",
            f"[{_ts_label(_ts(20, 10))} 游戏·minecraft·report] 本次目标交付：蜂蜜胶已做好并收进背包",
            f"[{_ts_label(_ts(20, 7))} 遭遇·minecraft·died] 角色倒下了：被苦力怕炸死（已结束）",
            f"[{_ts_label(_ts(20, 8))} 遭遇·minecraft·respawned] 角色在床边重生",
        ]
    )
    assert ledger.drain().facts_text == expected


def _ts_label(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M")


def test_facts_select_state_lines_and_skip_noise() -> None:
    """事实块只收状态跃迁条目：交付/死亡/重生/关键词行进块；挨打、游荡、聊天行跳过。"""
    ledger = GameLedger()
    _stage_fixture(ledger)
    batch = ledger.drain()
    lines = batch.facts_text.splitlines()
    assert any("交付：蜂蜜胶已做好" in line for line in lines)
    assert any("角色倒下了" in line and "（已结束）" in line for line in lines)
    assert any("重生" in line for line in lines)
    assert any("采集完成" in line for line in lines)
    assert not any("挨了一阵打" in line for line in lines)
    assert not any("苦力怕在游荡" in line for line in lines)
    assert not any("几点天亮" in line for line in lines)
    assert any(line.startswith("[20:10") for line in lines)


def test_no_state_events_yields_empty_facts() -> None:
    """无状态事件：只有非状态条目时事实块为空串（调用方整段省略）。"""
    ledger = GameLedger()
    ledger.record_event("minecraft", "attention_required", "附近有苦力怕在游荡", _ts(20, 5))
    ledger.record_encounter("minecraft", "attacked", "身体挨了一阵打", False, _ts(20, 3))
    assert ledger.drain().facts_text == ""


def test_fold_same_summary_within_window() -> None:
    """折叠窗内同摘要连发：并入最近一条只记数（"另 N 次相同"）。"""
    ledger = GameLedger()
    ledger.record_event("minecraft", "report", "交付 1 号", 1000)
    ledger.record_event("minecraft", "report", "交付 1 号", 2000)
    ledger.record_event("minecraft", "report", "交付 1 号", 3000)
    batch = ledger.drain()
    assert len(batch.events) == 1
    assert "另 2 次相同" in batch.events[0]
    assert batch.facts_text == batch.events[0]


def test_different_summaries_not_folded() -> None:
    """不同摘要不折叠：各成一条。"""
    ledger = GameLedger()
    ledger.record_event("minecraft", "report", "交付 1 号", 1000)
    ledger.record_event("minecraft", "report", "交付 2 号", 2000)
    assert len(ledger.drain().events) == 2


def test_state_lines_cap_keeps_most_recent() -> None:
    """状态行超上限时只留最近的 8 条。"""
    ledger = GameLedger()
    for i in range(12):
        ledger.record_event("minecraft", "report", f"交付 {i} 号", _ts(20, i % 60))
    facts = ledger.drain().facts_text
    assert len(facts.splitlines()) == 8
    assert "交付 11 号" in facts and "交付 0 号" not in facts


def test_delegation_turn_not_in_batch() -> None:
    """委派行只构造时间线行（直写时间线用），不进决策窗批次。"""
    ledger = GameLedger()
    turn = ledger.record_delegation("minecraft", "做一个蜂蜜胶")
    assert "我在 minecraft 里开始做" in turn.content
    assert "做一个蜂蜜胶" in turn.content
    batch = ledger.drain()
    assert batch.events == () and batch.facts_text == ""


def test_tool_receipt_key_fields_appended() -> None:
    """带工具回执：glance 形态回执的身体/背包/工作要点各成一行；场景列表不进。"""
    facts = GameLedger.compose_facts("", [("glance", _RECEIPT)])
    assert "[glance回执]" in facts
    assert '身体：dimension="minecraft:overworld"，health=20.0，food=18，held="铁镐"' in facts
    assert "当前背包：橡木原木 ×8、床 ×1、铁锭 ×3" in facts
    assert "手头工作：" in facts and "蜂蜜胶" in facts
    assert "creeper" not in facts


def test_receipt_non_glance_shape_skipped() -> None:
    """非速览形态回执不进块。"""
    assert GameLedger.compose_facts("", [("streamer_reply", {"ok": True})]) == ""


def test_compose_facts_receipts_follow_event_lines() -> None:
    """完整事实文本 = 事实行在前、回执行在后（与旧提取器的拼接顺序一致）。"""
    ledger = GameLedger()
    ledger.record_event("minecraft", "report", "交付完成", _ts(20, 10))
    batch = ledger.drain()
    composed = GameLedger.compose_facts(batch.facts_text, [("glance", _RECEIPT)])
    assert composed.splitlines()[0].startswith("[20:10")
    assert "[glance回执]" in composed.splitlines()[-1]


# ---------------------------------------------------------------------------
# 接线：事实块经批次 facts_text 进参考段与 reply 槽位；空块占位不出现字面变量
# ---------------------------------------------------------------------------


async def _capture_turn_input(state_facts: str) -> str:
    """用固定渲染桩驱动 Replyer，返回模板收到的 state_facts 值。"""
    from unittest.mock import MagicMock

    from src.agents.streamer.plan import DecisionPlan
    from src.agents.streamer.replyer import Replyer

    prompt = MagicMock()
    prompt.render = MagicMock(return_value="TURN")
    llm = MagicMock()
    llm.generate = MagicMock()

    async def _generate(*args: Any, **kwargs: Any) -> Any:
        class _R:
            success = True
            content = "好的"

        return _R()

    llm.generate = _generate
    replyer = Replyer({}, llm, prompt)
    plan = DecisionPlan(should_reply=True, topic_summary="状态同步")
    await replyer.generate(plan, [], state_facts=state_facts)
    return prompt.render.call_args.kwargs["state_facts"]


@pytest.mark.asyncio
async def test_replyer_renders_state_facts_or_placeholder() -> None:
    """事实块原样进"当前状态（客观事实）"分节；空块占位为"（无）"。"""
    line = "[20:10 游戏·minecraft·report] 交付完成"
    assert await _capture_turn_input(line) == line
    assert await _capture_turn_input("") == "（无）"


@pytest.mark.asyncio
async def test_planner_hands_state_facts_to_reply_tool() -> None:
    """批次 facts_text 与回执合成事实块随 reply 交给表达侧，调用后槽位清空。"""
    from unittest.mock import AsyncMock, MagicMock

    from src.agents.streamer.planner import Planner
    from src.agents.streamer.room_state import RoomState
    from src.agents.streamer.tools.reply_tool import ReplyToolProvider
    from src.modules.llm.payload import Response, ToolCall
    from src.modules.tools.models import ToolExecutionResult

    seen: List[str] = []
    replyer = MagicMock()

    async def _generate(**kwargs: Any) -> Dict[str, Any]:
        seen.append(kwargs["state_facts"])
        return {"speech": "刚做完一件事", "emotion": {"name": "happy", "intensity": 0.5}, "metadata": {}}

    replyer.generate = AsyncMock(side_effect=_generate)
    provider = ReplyToolProvider(replyer=replyer)
    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])

    async def _invoke(invocation: Any) -> ToolExecutionResult:
        if invocation.tool_name == "streamer_reply":
            return await provider.invoke(invocation)
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            structured_content={"body": {"health": 12.0}},
        )

    registry.invoke = AsyncMock(side_effect=_invoke)
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(
            success=True,
            content="",
            tool_calls=[
                ToolCall(id="c1", name="glance", arguments={}),
                ToolCall(id="c2", name="streamer_reply", arguments={"topic_summary": "交付完成", "is_novel": True}),
            ],
        )
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config={"planner_max_steps": 2},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        reply_provider=provider,
    )

    # 批次由账本真实出账：事实文本随批次携带，planner 不再自己从行提取
    ledger = GameLedger()
    ledger.record_event("minecraft", "report", "本次目标交付：蜂蜜胶已做好并收进背包", 0)
    batch = ledger.drain()

    outcome = await planner.plan([], proactive=True, game_batch=batch)

    assert outcome.replied is True
    assert len(seen) == 1
    assert "交付：蜂蜜胶已做好并收进背包" in seen[0]
    assert "身体：health=12.0" in seen[0]
    # 事实块随调用单一次性传入：Provider 上不再有跨调用槽位
    assert not hasattr(provider, "_round_state_facts")
