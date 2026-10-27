"""live_chat 单一事实源读取链路：会话历史、场次切换、时间线笔记与话题摘要输入"""

from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.agents.streamer.config import StreamerBackgroundConfig
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import GAME_MESSAGE_TYPE
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.llm.payload import Response
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase
from tests.agents.streamer.helpers import make_streamer_agent, stub_prompt


class _FakeSessionManager:
    def __init__(self, pk: Optional[int]) -> None:
        self.pk = pk

    async def resolve_pk(self) -> Optional[int]:
        return self.pk


async def _chat(store: SQLiteDatabase, ts: int, role: str, content: str, session: int = 1, mid: str = "") -> None:
    await store.chat.insert_live_chat(
        live_session_id=session,
        timestamp_ms=ts,
        sender_role=role,
        sender_name="观众" if role == "viewer" else "主播",
        content=content,
        message_type="danmaku" if role == "viewer" else "speak",
        message_id=mid or None,
    )


async def _viewer_turns(store: SQLiteDatabase, start: int, count: int, session: int = 1) -> None:
    for i in range(start, start + count):
        await _chat(store, 1000 + i, "viewer", f"消息{i}", session=session, mid=f"m{i}")


def _agent(store: SQLiteDatabase, pk: Optional[int]):
    return make_streamer_agent(chat_repo=store.chat, session_manager=_FakeSessionManager(pk))


async def test_session_index_and_role_filter(store: SQLiteDatabase) -> None:
    index = "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_live_chat_session_ts'"
    assert len(await store.execute(index)) == 1
    again = SQLiteDatabase(store.db_path)
    await again.initialize()  # 索引 DDL 幂等
    await again.close()

    assert await store.chat.list_recent_live_chat(live_session_id=1, sender_role="viewer") == []
    await _chat(store, 100, "viewer", "你好")
    await _chat(store, 200, "assistant", "欢迎")
    rows = await store.chat.list_recent_live_chat(live_session_id=1, sender_role="viewer")
    assert [r.content for r in rows] == ["你好"]


@pytest.mark.parametrize("pk", [None, 1], ids=["no_session", "empty_session"])
async def test_read_history_without_rows_is_empty(store: SQLiteDatabase, pk) -> None:
    assert await _agent(store, pk)._read_history() == []


async def test_history_is_chronological_and_append_only(store: SQLiteDatabase) -> None:
    """窗口内前缀逐字稳定（前缀缓存前提）；跨过旧条数限制后最初指令仍在。"""
    agent = _agent(store, 1)
    await _chat(store, 100, "viewer", "先问")
    await _chat(store, 200, "assistant", "后答")
    assert [(t.role, t.content) for t in await agent._read_history()] == [("viewer", "先问"), ("assistant", "后答")]

    await _viewer_turns(store, 1, 45)
    first = await agent._read_history()
    await _viewer_turns(store, 46, 3)
    second = await agent._read_history()
    assert [t.message_id for t in second][2:] == [f"m{i}" for i in range(1, 49)]
    assert second[: len(first)] == first


async def test_history_resets_on_session_switch(store: SQLiteDatabase) -> None:
    session = _FakeSessionManager(1)
    agent = make_streamer_agent(chat_repo=store.chat, session_manager=session)
    await _viewer_turns(store, 1, 6)
    await agent._read_history()

    session.pk = 2
    assert await agent._read_history() == []
    await _viewer_turns(store, 101, 2, session=2)
    assert [t.message_id for t in await agent._read_history()] == ["m101", "m102"]


async def test_game_reports_and_delegations_enter_timeline_in_time_order(store: SQLiteDatabase) -> None:
    """游戏事件与我开始做的事以发生时刻进时间线，排在同时段聊天之间；开播时清空。"""
    agent = _agent(store, 1)
    await _viewer_turns(store, 1, 2)
    report = GamePayload(game="minecraft", event_type="report", message="木头砍好了", timestamp_ms=1001)
    await agent._on_game_event("game.report", report, "test")
    await _viewer_turns(store, 5, 1)

    # 窗边界出账（决策执行器每轮第一步）先行，读历史是纯读
    agent._scope.drain_window()
    view = await agent._read_history()
    assert [getattr(turn, "message_id", "") or "note" for turn in view] == ["m1", "note", "m2", "m5"]
    # 行首是发生时刻的钟点，写入后不再变化
    assert view[1].message_type == GAME_MESSAGE_TYPE
    assert view[1].content.endswith(" 游戏·minecraft·report] 木头砍好了")

    # 读历史不改任何状态：再读一遍逐字一致（出账只发生在窗边界）
    assert await agent._read_history() == view

    agent._record_delegation("minecraft", "去砍点木头")
    later = await agent._read_history()
    assert later[: len(view)] == view and later[-1].content.endswith("我在 minecraft 里开始做] 去砍点木头")

    await agent._on_live_started("live.started", LiveStartedPayload(live_session_id=2), "test")
    assert (await agent._scope.open_window()).timeline_notes == []


async def test_read_history_is_pure_read_without_window_boundary(store: SQLiteDatabase) -> None:
    """读历史不代收窗边界职责：待入账游戏事件未出账前不进投影，批次保持原样。"""
    agent = _agent(store, 1)
    await _viewer_turns(store, 1, 1)
    report = GamePayload(game="minecraft", event_type="report", message="木头砍好了", timestamp_ms=1001)
    await agent._on_game_event("game.report", report, "test")

    view = await agent._read_history()

    assert [getattr(turn, "message_id", "") or "note" for turn in view] == ["m1"], "未出账的游戏事件不在时间线里"
    batch_before = agent._scope.current_window_batch()
    assert await agent._read_history() == view
    assert agent._scope.current_window_batch() is batch_before, "读历史不覆写本窗批次"


def _maintainer(store: SQLiteDatabase, pk: Optional[int] = 1, prompt_manager=None):
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="观众在聊新版本更新"))
    maintainer = BackgroundMaintainer(
        StreamerBackgroundConfig(enabled=True),
        room_state=RoomState(),
        llm_service=llm,
        session_manager=_FakeSessionManager(pk),
        chat_repo=store.chat,
        prompt_manager=prompt_manager or stub_prompt(),
    )
    return maintainer, llm


async def test_topic_summary_reads_viewer_rows(store: SQLiteDatabase) -> None:
    """弹幕进 live_chat 即进话题摘要输入；摘要写入房间态势供主动发言的话题门使用。"""
    prompt_manager = PromptManager(auto_scan_src=True)
    prompt_manager.load_all()
    maintainer, llm = _maintainer(store, prompt_manager=prompt_manager)
    await _chat(store, 100, "viewer", "新版本什么时候上线")

    await maintainer._summarize_topic(now_ms=200_000)
    assert "新版本什么时候上线" in llm.generate.await_args.args[0]
    assert maintainer._room_state.get_snapshot(now_ms=300_000).topic_summary == "观众在聊新版本更新"


@pytest.mark.parametrize("pk", [None, 1], ids=["no_session", "only_assistant_rows"])
async def test_topic_summary_skips_without_viewer_input(store: SQLiteDatabase, pk) -> None:
    """无场次静默跳过；窗口内只有主播发言则清空摘要，防自嗨循环。"""
    maintainer, llm = _maintainer(store, pk)
    maintainer._room_state.set_topic_summary("旧话题", now_ms=1)
    await _chat(store, 100, "assistant", "大家好")

    await maintainer._summarize_topic(now_ms=200_000)
    llm.generate.assert_not_awaited()
    expected = "旧话题" if pk is None else ""
    assert maintainer._room_state.get_snapshot(now_ms=300_000).topic_summary == expected
