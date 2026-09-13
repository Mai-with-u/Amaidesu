"""live_chat 单一事实源读取链路测试（ContextService 删除后）。

覆盖：
- 组合索引 ``idx_live_chat_session_ts`` 随 initialize 建立（幂等 DDL）
- ``list_recent_live_chat`` 的 sender_role 过滤与空表空读
- ``StreamerAgent._read_history``：无显式场次 / 空场次 → 空列表，不抛错
- ``BackgroundMaintainer._summarize_topic``：live_chat viewer 行 → topic_summary
  可被写入（摘要链路复活：弹幕进 live_chat 即进话题摘要输入）
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.streamer_agent import StreamerAgent
from src.agents.streamer.config import StreamerConfig
from src.modules.llm.manager import LLMResponse
from src.modules.storage.sqlite_store import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="streamer-live-chat-read-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


class _FakeSessionManager:
    """resolve_pk 返回固定主键；pk=None 表示无显式场次。"""

    def __init__(self, pk) -> None:
        self._pk = pk

    async def resolve_pk(self):
        return self._pk


# ---------------------------------------------------------------------------
# 索引
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_creates_live_chat_session_ts_index(store: SQLiteStore) -> None:
    """全新库 initialize → sqlite_master 含组合索引。"""
    rows = await store.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_live_chat_session_ts'"
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_initialize_index_idempotent(store: SQLiteStore) -> None:
    """重复 initialize 不因索引 DDL 报错（IF NOT EXISTS 幂等）。"""
    store2 = SQLiteStore(store.db_path)
    await store2.initialize()
    await store2.close()


# ---------------------------------------------------------------------------
# 读取改道：sender_role 过滤 + 空读
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recent_live_chat_role_filter(store: SQLiteStore) -> None:
    """sender_role='viewer' 只取观众行；空表返回 []。"""
    empty = await store.list_recent_live_chat(live_session_id=1, sender_role="viewer")
    assert empty == []

    await store.insert_live_chat(
        live_session_id=1, timestamp_ms=100, sender_role="viewer",
        sender_name="观众A", content="你好", message_type="danmaku",
    )
    await store.insert_live_chat(
        live_session_id=1, timestamp_ms=200, sender_role="assistant",
        sender_name="主播", content="欢迎", message_type="speak",
    )
    rows = await store.list_recent_live_chat(live_session_id=1, sender_role="viewer")
    assert [r["content"] for r in rows] == ["你好"]


def _make_agent(sqlite_store, session_manager) -> StreamerAgent:
    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=LLMResponse(success=False, error="not used"))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    return StreamerAgent(
        config=StreamerConfig.from_dict({"proactive": {"enabled": False}}),
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=None,
        tool_registry=None,
        sqlite_store=sqlite_store,
        session_manager=session_manager,
    )


@pytest.mark.asyncio
async def test_read_history_no_active_session_returns_empty(store: SQLiteStore) -> None:
    """无显式场次（首场/未开播）→ 空列表，不抛错。"""
    agent = _make_agent(store, _FakeSessionManager(None))
    history = await agent._read_history()
    assert history == []


@pytest.mark.asyncio
async def test_read_history_empty_session_returns_empty(store: SQLiteStore) -> None:
    """场次存在但 live_chat 无行（首决定窗）→ 空列表，不抛错。"""
    agent = _make_agent(store, _FakeSessionManager(1))
    history = await agent._read_history()
    assert history == []


@pytest.mark.asyncio
async def test_read_history_returns_turns_in_chronological_order(store: SQLiteStore) -> None:
    """历史按时间正序返回，role 承载 sender_role。"""
    await store.insert_live_chat(
        live_session_id=1, timestamp_ms=100, sender_role="viewer",
        sender_name="观众A", content="先问", message_type="danmaku",
    )
    await store.insert_live_chat(
        live_session_id=1, timestamp_ms=200, sender_role="assistant",
        sender_name="主播", content="后答", message_type="speak",
    )
    agent = _make_agent(store, _FakeSessionManager(1))
    history = await agent._read_history()
    assert [(t.role, t.content) for t in history] == [
        ("viewer", "先问"),
        ("assistant", "后答"),
    ]


# ---------------------------------------------------------------------------
# 摘要链路复活：live_chat viewer 行 → topic_summary
# ---------------------------------------------------------------------------


def _make_maintainer(store: SQLiteStore) -> tuple[BackgroundMaintainer, MagicMock]:
    room_state = RoomState()
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=LLMResponse(success=True, content="观众在聊新版本更新")
    )
    memory = MagicMock()
    memory.ingest = AsyncMock(return_value=None)
    maintainer = BackgroundMaintainer(
        {"enabled": True},
        room_state=room_state,
        llm_service=llm,
        session_manager=_FakeSessionManager(1),
        memory=memory,
        sqlite_store=store,
    )
    return maintainer, llm


@pytest.mark.asyncio
async def test_summarize_topic_reads_live_chat_viewer_rows(store: SQLiteStore) -> None:
    """seed live_chat viewer 行 → 摘要 LLM 收到弹幕文本，topic_summary 被写入。"""
    maintainer, llm = _make_maintainer(store)
    await store.insert_live_chat(
        live_session_id=1, timestamp_ms=100, sender_role="viewer",
        sender_name="观众A", content="新版本什么时候上线", message_type="danmaku",
    )

    await maintainer._summarize_topic(now_ms=200_000)

    # LLM 输入含观众弹幕（摘要输入不再恒空）
    prompt_text = llm.chat.await_args.kwargs["prompt"]
    assert "新版本什么时候上线" in prompt_text
    # topic_summary 写入房间态势（下游 ProactiveTrigger 的 topic 门据此放行）
    snap = maintainer._room_state.get_snapshot(now_ms=300_000)
    assert snap.topic_summary == "观众在聊新版本更新"


@pytest.mark.asyncio
async def test_summarize_topic_no_active_session_is_noop(store: SQLiteStore) -> None:
    """无显式场次 → 静默跳过（不调 LLM、不写摘要）。"""
    room_state = RoomState()
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=LLMResponse(success=True, content="x"))
    maintainer = BackgroundMaintainer(
        {"enabled": True},
        room_state=room_state,
        llm_service=llm,
        session_manager=_FakeSessionManager(None),
        sqlite_store=store,
    )

    await maintainer._summarize_topic(now_ms=200_000)

    llm.chat.assert_not_awaited()
    assert room_state.get_snapshot(now_ms=300_000).topic_summary == ""


@pytest.mark.asyncio
async def test_summarize_topic_only_assistant_rows_clears_summary(store: SQLiteStore) -> None:
    """窗口内只有主播发言（无观众弹幕）→ 清空 topic_summary 防自嗨循环。"""
    maintainer, llm = _make_maintainer(store)
    maintainer._room_state.set_topic_summary("旧话题", now_ms=1)
    await store.insert_live_chat(
        live_session_id=1, timestamp_ms=100, sender_role="assistant",
        sender_name="主播", content="大家好", message_type="speak",
    )

    await maintainer._summarize_topic(now_ms=200_000)

    llm.chat.assert_not_awaited()
    snap = maintainer._room_state.get_snapshot(now_ms=300_000)
    assert snap.topic_summary == ""
