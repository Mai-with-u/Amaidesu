"""对话连贯性回归测试——主播记得自己刚说过什么、看得到观众原话。

覆盖：
- 未开场次 / 调试注入：本轮观众消息与主播发言记入内存，下一轮历史可见
- 开着场次：落库行与未落库的调试消息按时间合并，已落库的不重复
- 场次边界清空内存对话轮
- Planner 把本批弹幕交给 reply 工具，表达侧不再误判为"本批无弹幕"
- 同一步里 reply 与其他工具并列时，其他工具先执行、reply 最后收尾
- 历史尾部与本批同源的消息去重（Planner / Replyer 共用）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer import canonical
from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.streamer_agent import StreamerAgent
from src.agents.streamer.tools.reply_tool import ReplyToolProvider
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolExecutionResult, ToolInvocation


@dataclass(frozen=True)
class FakeTurn:
    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""


class _FakeSessionManager:
    """resolve_pk 返回可切换的主键；None 表示没开场次。"""

    def __init__(self, pk: Optional[int]) -> None:
        self.pk = pk

    async def resolve_pk(self) -> Optional[int]:
        return self.pk


class _FakeChatRepo:
    """live_chat 读面替身：按时间正序返回预置行。"""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def list_recent_live_chat(self, *, live_session_id: int, limit: Optional[int] = None) -> List[Dict]:
        return list(self.rows)


def _msg(text: str, mid: str, *, nickname: str = "调试观众", ts: int = 0) -> RoomMessagePayload:
    kwargs: Dict[str, Any] = {"timestamp_ms": ts} if ts else {}
    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id=f"debug_{nickname}", name=nickname),
        content=text,
        message_id=mid,
        **kwargs,
    )


def _make_agent(*, chat_repo: Any = None, session_manager: Any = None) -> StreamerAgent:
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    return StreamerAgent(
        config=StreamerConfig.from_dict({"proactive": {"enabled": False}}),
        llm_manager=MagicMock(),
        prompt_manager=prompt,
        event_bus=None,
        tool_registry=None,
        chat_repo=chat_repo,
        session_manager=session_manager,
    )


# ---------------------------------------------------------------------------
# 内存对话轮
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unopened_session_keeps_last_question_for_next_round() -> None:
    """没开场次：主播问"要不要拉一下"，观众回"拉"时，历史里有上一问一答。"""
    agent = _make_agent(chat_repo=_FakeChatRepo([]), session_manager=_FakeSessionManager(None))
    agent._rounds.execute = AsyncMock(return_value={"speech": "要不要现场拉一下给大家看？"})

    result = await agent.debug_test_decision(batch=[{"nickname": "调试观众", "text": "麦麦会操作这个机器吗"}])
    assert result["success"] is True

    history = await agent._read_history()
    assert [(turn.role, turn.content) for turn in history] == [
        ("viewer", "麦麦会操作这个机器吗"),
        ("assistant", "要不要现场拉一下给大家看？"),
    ]
    # 下一轮决策照常读到同一份历史（不是只记最后一条）
    assert canonical.turn_to_message(history[0])["content"].startswith("调试观众: 麦麦会操作这个机器吗")


@pytest.mark.asyncio
async def test_open_session_merges_debug_message_with_stored_rows_by_time() -> None:
    """开着场次：调试注入的观众消息不落库，按发生时刻并入 live_chat 行；已落库的发言不重复。"""
    rows = [
        {
            "sender_role": "assistant",
            "content": "要不要现场拉一下？",
            "sender_name": "主播",
            "message_type": "speak",
            "message_id": None,
            "timestamp_ms": 2000,
        }
    ]
    agent = _make_agent(chat_repo=_FakeChatRepo(rows), session_manager=_FakeSessionManager(7))
    agent._rounds.execute = AsyncMock(return_value={"speech": "要不要现场拉一下？"})

    await agent._execute_round(
        [_msg("麦麦会操作这个机器吗", "d1", ts=1000)],
        forced=True,
        trigger_reason="dashboard:debug_test",
        batch_persisted=False,
    )

    history = await agent._read_history()
    assert [(turn.role, turn.content) for turn in history] == [
        ("viewer", "麦麦会操作这个机器吗"),
        ("assistant", "要不要现场拉一下？"),
    ]


@pytest.mark.asyncio
async def test_open_session_normal_batch_not_duplicated_in_memory() -> None:
    """开着场次的正常弹幕已经由落库链路写入 live_chat，内存不再另记一份。"""
    agent = _make_agent(chat_repo=_FakeChatRepo([]), session_manager=_FakeSessionManager(7))
    agent._rounds.execute = AsyncMock(return_value={"speech": "好嘞"})

    await agent._execute_round([_msg("拉", "m1", ts=1000)], forced=False, trigger_reason="window_expired")

    assert agent._unpersisted_turns == []


@pytest.mark.asyncio
async def test_live_started_clears_memory_turns() -> None:
    """开播即清空开播前的调试对话，这一场历史从 live_chat 重新开始。"""
    agent = _make_agent(session_manager=_FakeSessionManager(None))
    agent._rounds.execute = AsyncMock(return_value={"speech": "收到"})
    await agent.debug_test_decision(batch=[{"nickname": "调试观众", "text": "开播前随便聊聊"}])
    assert len(agent._unpersisted_turns) == 2

    await agent._on_live_started(
        "live.started",
        LiveStartedPayload(live_session_id=1, source="manual", started_at_ms=1),
        "test",
    )

    assert agent._unpersisted_turns == []


# ---------------------------------------------------------------------------
# reply 工具拿到本批弹幕 + 同步执行顺序
# ---------------------------------------------------------------------------


def _planner_with(responses: List[Response], registry: MagicMock, reply_provider: Any) -> Planner:
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=responses)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config={"planner_max_steps": 3},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        reply_provider=reply_provider,
    )
    return planner


@pytest.mark.asyncio
async def test_reply_runs_after_sibling_tools_in_same_step() -> None:
    """模型把 reply 写在环节切换前面：环节切换仍被执行，且先于 reply。"""
    invoked: List[str] = []

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        invoked.append(invocation.tool_name)
        if invocation.tool_name == "streamer_reply":
            return ToolExecutionResult(
                tool_name="streamer_reply",
                success=True,
                structured_content={"speech": "下一个环节！", "emotion": {"name": "happy", "intensity": 0.5}},
            )
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content={"ok": True})

    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=_invoke)
    step = Response(
        success=True,
        content="",
        tool_calls=[
            ToolCall(id="c1", name="streamer_reply", arguments={"topic_summary": "切环节"}),
            ToolCall(id="c2", name="rundown_control", arguments={"action": "next"}),
        ],
    )
    planner = _planner_with([step], registry, MagicMock())

    outcome = await planner.plan([_msg("拉", "m1")])

    assert outcome["replied"] is True
    assert invoked == ["rundown_control", "streamer_reply"]


@pytest.mark.asyncio
async def test_planner_hands_round_batch_to_reply_tool() -> None:
    """reply 调用期间 reply 工具持有本批弹幕，调用结束后槽位清空。"""
    seen_batches: List[List[Any]] = []
    replyer = MagicMock()

    async def _generate(*, plan: Any, batch: List[Any], **_: Any) -> Dict[str, Any]:
        seen_batches.append(list(batch))
        return {"speech": "拉就拉！", "emotion": {"name": "happy", "intensity": 0.5}, "metadata": {}}

    replyer.generate = AsyncMock(side_effect=_generate)
    provider = ReplyToolProvider(replyer=replyer)
    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=provider.invoke)
    step = Response(
        success=True,
        content="",
        tool_calls=[ToolCall(id="c1", name="streamer_reply", arguments={"topic_summary": "回应拉"})],
    )
    planner = _planner_with([step], registry, provider)
    batch = [_msg("拉", "m1")]

    outcome = await planner.plan(batch)

    assert outcome["replied"] is True
    assert [[m.content for m in b] for b in seen_batches] == [["拉"]]
    assert provider._round_batch == []


def test_trim_batch_echo_drops_only_tail_copy_of_batch() -> None:
    """历史尾部与本批同源的行剔除；更早的同文历史保留。"""
    history = [
        FakeTurn(role="viewer", content="拉", message_id="old"),
        FakeTurn(role="assistant", content="要不要拉一下？"),
        FakeTurn(role="viewer", content="拉", message_id="m1"),
    ]

    trimmed = canonical.trim_batch_echo(history, [_msg("拉", "m1")])

    assert [turn.content for turn in trimmed] == ["拉", "要不要拉一下？"]


# ---------------------------------------------------------------------------
# 叙事时间标注 + 付费情境
# ---------------------------------------------------------------------------


def test_narrative_marks_age_and_new_since_last_round() -> None:
    """旧条目标到达距今多久，上次决策后才到的标"新"——死亡不会被当成刚发生的事反复讲。"""
    from src.agents.streamer.streamer_agent import _NarrativeEntry, _render_narrative

    entries = [
        _NarrativeEntry(received_ms=0, line="[minecraft·died] 死了"),
        _NarrativeEntry(received_ms=40 * 60_000, line="[minecraft·report] 蜂房机器运转正常"),
    ]

    text = _render_narrative(entries, seen_until_ms=20 * 60_000, now=40 * 60_000 + 5_000)

    assert text.splitlines() == [
        "[40 分钟前] [minecraft·died] 死了",
        "[新·刚刚] [minecraft·report] 蜂房机器运转正常",
    ]


@pytest.mark.asyncio
async def test_forced_debug_batch_is_not_labeled_as_paid() -> None:
    """控制台点名必答，但没人付费：情境不说 SC，真有醒目留言时才说付费点名。"""
    captured: List[List[dict]] = []

    async def _generate(messages: List[dict], **_: Any) -> Response:
        captured.append([dict(m) for m in messages])
        return Response(success=True, content="不说")

    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    planner = _planner_with([], registry, MagicMock())
    planner._llm_service.generate = AsyncMock(side_effect=_generate)

    await planner.plan([_msg("拉", "m1")], forced=True)
    paid = RoomMessagePayload(
        message_type="super_chat", user=RoomMessageUser(id="u2", name="老板"), content="冲", message_id="m2"
    )
    await planner.plan([paid], forced=True)

    debug_ref, paid_ref = captured[0][-1]["content"], captured[1][-1]["content"]
    assert "运营点名的必答消息" in debug_ref and "SC" in debug_ref and "付费点名" not in debug_ref
    assert "含付费消息：SC" in paid_ref


@pytest.mark.asyncio
async def test_registry_observation_keeps_one_copy_of_duplicated_json_body() -> None:
    """MCP 工具正文只是结构化结果的 JSON 时只给一份；正文另有信息时照常附上。"""
    import json

    structured = {"health": 20.0, "position": {"x": -86, "y": 105, "z": 27}}
    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    planner = _planner_with([], registry, MagicMock())

    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="x_tool", success=True, structured_content=structured, content=json.dumps(structured)
        )
    )
    duplicated = json.loads(await planner._invoke_registry_tool("x_tool", {}))
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="x_tool", success=True, structured_content=structured, content="附近有一块写着蜂房的牌子"
        )
    )
    distinct = json.loads(await planner._invoke_registry_tool("x_tool", {}))

    assert "content" not in duplicated and duplicated["health"] == 20.0
    assert distinct["content"] == "附近有一块写着蜂房的牌子"
