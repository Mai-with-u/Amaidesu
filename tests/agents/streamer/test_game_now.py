"""【游戏那边此刻】：每窗重建一块游戏里此刻的读数，放在 Planner 参考段最后，只给 Planner。

第 58 场她把楼上有平台的房子说成"就一层"、说"花一直在包里"：新读的现场夹在参考段中间，后面几十行
已聊话题各带一份十几二十分钟前的状态快照，新读数被埋住。这里钉住这一块的内容、先后、封顶与读数来处。
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.game_now import AgentNow, GameNow, GameSideLog, render_game_now, scrub_game_readings
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.planner_render import Delegation
from src.agents.streamer.room_state import RoomState
from src.modules.events.payloads.agents import AgentRepliedPayload
from src.modules.events.payloads.game_chat import GameSelfChattedPayload
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.llm.context_meter import get_encoding
from src.modules.llm.payload import Response
from src.modules.tools.models import ToolExecutionResult, ToolInvocation
from src.modules.tools.tasks import TaskLedger
from src.modules.utils.time_utils import now_ms
from tests.agents.streamer.helpers import FakeRoundExecutor, make_streamer_agent

_MINUTE = 60_000
_NOW = 100 * _MINUTE


def _delegation(**overrides: Any) -> Delegation:
    fields: dict = {
        "agent": "minecraft",
        "instruction": "把营地的门装好",
        "task_id": "deleg_1",
        "at_ms": _NOW - 10 * _MINUTE,
    }
    fields.update(overrides)
    return Delegation(**fields)


def _record(notes: List[str], status: str = "running", **snapshot: Any) -> Any:
    """委派在任务账本里的机器状态：快照带游戏侧进展备注（另可带停滞标记等）。"""
    data = {"game_progress_notes": notes, **snapshot}
    return lambda _task_id: {"status": status, "updated_at_ms": _NOW, "snapshot": data}


def test_block_lists_task_step_results_judgement_chat_then_overview() -> None:
    """一件手上的事的几行读数在前，她近 5 分钟的公屏话随后，现场速览放在最后；结果换成中文、内部编号脱掉。
    委派原话只在前段写一遍，这里不重复；状态里那一步结果下面另有一行，不写两遍。"""
    reading = AgentNow(
        step="去营地门口把门装上",
        step_at_ms=_NOW - 2 * _MINUTE,
        judgement="门框左边差一格木板\n先补木板再装门",
        judgement_at_ms=_NOW - 30_000,
    )
    notes = [
        "游戏目标 568（maicraft:find）failed：这一片没有找到蒲公英",
        "游戏目标 570（maicraft:obtain）partial：拿到 3 个，还差 5 个",
    ]
    now = GameNow(agents={"minecraft": reading}, self_chat=((_NOW - _MINUTE, "minecraft", "门马上就好"),))

    text = render_game_now({"minecraft": _delegation()}, _record(notes), now, '{"position": "营地平台"}', _NOW)

    assert text.splitlines() == [
        "【游戏那边此刻】（每轮重读；游戏里做到哪、背包里有什么、房子什么样，以这一块为准）",
        "- 在 minecraft 里手上那件事（原话见前面）：还在做，游戏那边有一步有结果了（见下面最近几步结果）。",
        "  此刻那一步：去营地门口把门装上（2 分钟前开始）",
        "  最近几步结果：没做成：这一片没有找到蒲公英；做了一半：拿到 3 个，还差 5 个",
        "  游戏那边的判断（不一定对）：门框左边差一格木板（刚刚）",
        "- 我近 5 分钟在游戏公屏说过：「门马上就好」（1 分钟前）",
        "【现场速览（本轮决策前自动读取）】",
        '{"position": "营地平台"}',
    ]


def test_step_and_judgement_from_before_this_task_are_left_out() -> None:
    """这件事开始之前的那一步和判断是上一件事的，不列；事做完了也不再列此刻那一步。"""
    stale = AgentNow(
        step="挖石头", step_at_ms=_NOW - 20 * _MINUTE, judgement="石头够了", judgement_at_ms=_NOW - 20 * _MINUTE
    )
    text = render_game_now({"minecraft": _delegation()}, None, GameNow(agents={"minecraft": stale}), "", _NOW)
    assert "此刻那一步" not in text and "游戏那边的判断" not in text

    fresh = AgentNow(step="装门", step_at_ms=_NOW - _MINUTE)
    done = _delegation(report_kind="delivery", reported_ms=_NOW)
    text = render_game_now({"minecraft": done}, None, GameNow(agents={"minecraft": fresh}), "", _NOW)
    assert "做完，结果见游戏记录" in text and "此刻那一步" not in text

    # 游戏那边没交付就停了：不说成做完了，此刻那一步也不再列
    stopped = _delegation(report_kind="undelivered", reported_ms=_NOW)
    text = render_game_now({"minecraft": stopped}, None, GameNow(agents={"minecraft": fresh}), "", _NOW)
    assert "停了，没交付结果，做没做完不确定" in text and "做完了" not in text and "此刻那一步" not in text


def test_state_matches_the_delegation_line_when_stuck_or_idle() -> None:
    """卡住的活不写成"还在做"了事：停着没动静、账面多久没进展，和前段委派状态同一句，她才会去拿主意。"""
    stalled = _record([], stalled_goals={"g1": "找蒲公英"})
    text = render_game_now({"minecraft": _delegation()}, stalled, GameNow(), "", _NOW)
    assert "：还在做，但游戏说有的目标停着没动静（找蒲公英）。" in text
    assert "把营地的门装好" not in text, "委派原话留在前段，不再写第二遍"

    idle = lambda _task_id: {"status": "running", "updated_at_ms": _NOW - 12 * _MINUTE, "snapshot": {}}  # noqa: E731
    text = render_game_now({"minecraft": _delegation()}, idle, GameNow(), "", _NOW)
    assert "：还在做，账面已 12 分钟前没有新进展。" in text
    waiting = render_game_now(
        {"minecraft": _delegation()}, _record([], status="waiting_for_decision"), GameNow(), "", _NOW
    )
    assert "停着等我和观众拿主意" in waiting


def test_readings_drop_coordinates_ids_and_tool_names() -> None:
    """游戏那一面的读数带坐标、编号、工具名（第 58 场的目的与判断句式）：坐标换成"那里"或去掉，其余去掉。"""
    cases = {
        "贴着 (-354,71,57) 的北面补上屋面缺格 (-354,71,56)": "贴着那里的北面补上屋面缺格",
        "在棚底正中地板 (-352,68,55) 上插一支火把": "在棚底正中地板上插一支火把",
        "挖掉 (-349,70,56) 多的一层土": "挖掉那里多的一层土",
        "苦力怕在 (-363, 65, 60)。先走到它附近再动手。": "苦力怕在那里。先走到它附近再动手。",
        "就差屋面中间 2 格（(-354,71,56)、(-352,71,56)）。": "就差屋面中间 2 格。",
        "床在 (-352,56~57)、箱子在 (-353,56/57) 和 (-350,57)，都坐在 y=68 的平台上": (
            "床在那里、箱子在那里和那里，都坐在那里的平台上"
        ),
        "把花放到东窗内侧地板上（留 z=56 给蒲公英）": "把花放到东窗内侧地板上（留那里给蒲公英）",
        "游戏目标 456（maicraft:travel）done：走到了目的地，实测位置 (-334, 64, 58)": "做完了：走到了目的地，实测位置",
        "读了 3 层方块，存成图纸 325B3760-3717-4A91-9449-8C8BE7034C0D": "读了 3 层方块，存成图纸",
        '先 maicraft_goal(action="cancel", goal_id=568) 再去打 e12（b5）': "先再去打那个",
        "goal_id=568 已取消，用 minecraft_glance 看一眼": "已取消，用看一眼",
        "拿到 6 个 minecraft:oak_log": "拿到 6 个 minecraft:oak_log",
    }
    assert {raw: scrub_game_readings(raw) for raw in cases} == cases

    reading = AgentNow(
        step="贴着 (-354,71,57) 的北面补上屋面缺格",
        step_at_ms=_NOW - _MINUTE,
        judgement="苦力怕在 (-363, 65, 60)。先走到它附近再动手。",
        judgement_at_ms=_NOW - _MINUTE,
    )
    notes = ["游戏目标 456（maicraft:travel）done：实测位置 (-334, 64, 58)"]
    text = render_game_now({"minecraft": _delegation()}, _record(notes), GameNow({"minecraft": reading}), "", _NOW)
    assert "(" not in text.split("【现场速览", 1)[0] and "maicraft" not in text


def test_block_is_empty_without_anything_and_overview_alone_still_shows() -> None:
    assert render_game_now({}, None, GameNow(), "", _NOW) == ""
    text = render_game_now({}, None, GameNow(), "看一眼的结果", _NOW)
    assert text.endswith("【现场速览（本轮决策前自动读取）】\n看一眼的结果")


def test_block_besides_overview_stays_within_600_tokens() -> None:
    """除现场速览外整块约 600 token 以内（cl100k 口径）：每项截短，公屏话至多 3 句。"""
    encoding = get_encoding()
    if encoding is None:
        pytest.skip("cl100k 词表不可用（离线且没有本地缓存），无法按 cl100k 口径计数")
    long = "把营地东边那片空地整平然后在中间盖一座两层的小木屋屋顶用深色橡木台阶门朝南还要留窗户" * 3
    reading = AgentNow(step=long, step_at_ms=_NOW - _MINUTE, judgement=long, judgement_at_ms=_NOW - _MINUTE)
    chat = tuple((_NOW - index * 1000, "minecraft", long) for index in range(10))
    notes = [f"游戏目标 {index}（maicraft:build）partial：{long}" for index in range(5)]

    text = render_game_now(
        {"minecraft": _delegation(instruction=long)}, _record(notes), GameNow({"minecraft": reading}, chat), "", _NOW
    )

    assert len(encoding.encode(text)) <= 600
    assert text.count("「") == 3


def test_side_log_keeps_last_judgement_and_five_minutes_of_self_chat() -> None:
    log = GameSideLog()
    log.note_judgement("minecraft", "先补木板", _NOW - 2 * _MINUTE)
    log.note_judgement("minecraft", "再装门", _NOW - _MINUTE)
    log.note_self_chat("minecraft", "老早说的", _NOW - 6 * _MINUTE)
    log.note_self_chat("minecraft", "门马上就好", _NOW - _MINUTE)

    assert log.judgement("minecraft") == ("再装门", _NOW - _MINUTE)
    assert [text for _at, _game, text in log.recent_self_chat(_NOW)] == ["门马上就好"]
    log.reset()
    assert log.judgement("minecraft") == ("", 0) and log.recent_self_chat(_NOW) == ()


async def test_planner_puts_the_block_last_with_the_overview_inside() -> None:
    """Planner 参考段最后一块是【游戏那边此刻】，现场速览在这一块里、不再夹在中间。"""
    captured: List[List[dict]] = []

    async def _generate(messages: List[dict], **_: Any) -> Response:
        captured.append([dict(message) for message in messages])
        return Response(success=True, content="不说")

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=invocation.tool_name, success=True, structured_content={"position": "营地平台"}
        )

    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=_invoke)
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=_generate)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    asked: List[List[str]] = []

    def _game_now(agents: Any) -> GameNow:
        asked.append(list(agents))
        return GameNow(agents={"minecraft": AgentNow(step="装门", step_at_ms=now_ms())})

    planner = Planner(
        config=PlannerConfig(planner_max_steps=1, reference_tools=["minecraft_glance"]),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(reply_provider_factory=lambda: MagicMock(), game_now_provider=_game_now),
    )
    planner.workbench.delegations["minecraft"] = Delegation(
        agent="minecraft", instruction="把营地的门装好", task_id="", at_ms=now_ms() - _MINUTE
    )

    await planner.plan([], proactive=True)

    reference = captured[0][-1]["content"]
    head, block = reference.split("【游戏那边此刻】", 1)
    assert "现场速览" not in head
    assert "此刻那一步：装门" in block and block.rstrip().endswith('"营地平台"}')
    assert asked == [["minecraft"]]


async def test_agent_feeds_step_judgement_and_self_chat_and_clears_them_at_live_start() -> None:
    """主播 Agent 的读数：任务账本里游戏 Agent 自己发起、还在跑的最近一步的目的，它最近一句判断，
    她近 5 分钟的公屏话；开播清掉上一场的判断和公屏话。"""
    ledger = TaskLedger()
    for task_id, purpose in (("goal_1", "挖石头"), ("goal_2", "去营地门口把门装上")):
        ledger.register(
            task_id=task_id,
            provider="maicraft",
            tool="maicraft_execute",
            initiator="minecraft",
            executor="maicraft",
            source="provider",
            snapshot={"purpose": purpose},
        )
    # 它接下的委派不是"一步"
    ledger.register(
        task_id="deleg_1",
        provider="minecraft",
        tool="delegate",
        initiator="streamer",
        executor="minecraft",
        source="agent",
    )
    agent = make_streamer_agent(task_ledger=ledger, round_executor=FakeRoundExecutor({"speech": "好"}))

    await agent._on_agent_replied(
        "agent.replied", AgentRepliedPayload(agent="minecraft", content="门框差一格木板"), "test"
    )
    await agent._on_self_chatted(
        "game.self.chatted", GameSelfChattedPayload(game="minecraft", message="门马上就好"), "test"
    )
    now = agent._game_now(["minecraft"])

    reading = now.agents["minecraft"]
    assert reading.step == "去营地门口把门装上" and reading.judgement == "门框差一格木板"
    assert [text for _at, _game, text in now.self_chat] == ["门马上就好"]

    started = LiveStartedPayload(live_session_id=1, source="manual", started_at_ms=1)
    await agent._on_live_started("live.started", started, "test")
    after = agent._game_now(["minecraft"])
    assert after.agents["minecraft"].judgement == "" and after.self_chat == ()
