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
from src.modules.llm.client import LLMResponse
from src.modules.llm.payload import Response
from src.modules.prompts import PromptManager
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="streamer-live-chat-read-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_db_path)
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
async def test_initialize_creates_live_chat_session_ts_index(store: SQLiteDatabase) -> None:
    """全新库 initialize → sqlite_master 含组合索引。"""
    rows = await store.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_live_chat_session_ts'")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_initialize_index_idempotent(store: SQLiteDatabase) -> None:
    """重复 initialize 不因索引 DDL 报错（IF NOT EXISTS 幂等）。"""
    store2 = SQLiteDatabase(store.db_path)
    await store2.initialize()
    await store2.close()


# ---------------------------------------------------------------------------
# 读取改道：sender_role 过滤 + 空读
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recent_live_chat_role_filter(store: SQLiteDatabase) -> None:
    """sender_role='viewer' 只取观众行；空表返回 []。"""
    empty = await store.chat.list_recent_live_chat(live_session_id=1, sender_role="viewer")
    assert empty == []

    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=100,
        sender_role="viewer",
        sender_name="观众A",
        content="你好",
        message_type="danmaku",
    )
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=200,
        sender_role="assistant",
        sender_name="主播",
        content="欢迎",
        message_type="speak",
    )
    rows = await store.chat.list_recent_live_chat(live_session_id=1, sender_role="viewer")
    assert [r["content"] for r in rows] == ["你好"]


def _make_agent(store: SQLiteDatabase, session_manager) -> StreamerAgent:
    llm = MagicMock()
    llm.call_tools = AsyncMock(return_value=LLMResponse(success=False, error="not used"))
    llm.generate = AsyncMock(return_value=Response(success=False, error="not used"))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    return StreamerAgent(
        config=StreamerConfig.from_dict({"proactive": {"enabled": False}}),
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=None,
        tool_registry=None,
        chat_repo=store.chat,
        session_manager=session_manager,
    )


@pytest.mark.asyncio
async def test_read_history_no_active_session_returns_empty(store: SQLiteDatabase) -> None:
    """无显式场次（首场/未开播）→ 空列表，不抛错。"""
    agent = _make_agent(store, _FakeSessionManager(None))
    history = await agent._read_history()
    assert history == []


@pytest.mark.asyncio
async def test_read_history_empty_session_returns_empty(store: SQLiteDatabase) -> None:
    """场次存在但 live_chat 无行（首决定窗）→ 空列表，不抛错。"""
    agent = _make_agent(store, _FakeSessionManager(1))
    history = await agent._read_history()
    assert history == []


@pytest.mark.asyncio
async def test_read_history_returns_turns_in_chronological_order(store: SQLiteDatabase) -> None:
    """历史按时间正序返回，role 承载 sender_role。"""
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=100,
        sender_role="viewer",
        sender_name="观众A",
        content="先问",
        message_type="danmaku",
    )
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=200,
        sender_role="assistant",
        sender_name="主播",
        content="后答",
        message_type="speak",
    )
    agent = _make_agent(store, _FakeSessionManager(1))
    history = await agent._read_history()
    assert [(t.role, t.content) for t in history] == [
        ("viewer", "先问"),
        ("assistant", "后答"),
    ]


# ---------------------------------------------------------------------------
# 历史窗口滞回：满窗后成块推进，窗口内前缀逐字稳定（前缀缓存前提）
# ---------------------------------------------------------------------------


async def _insert_turns(store: SQLiteDatabase, count: int, *, start_index: int, session_id: int = 1) -> None:
    """按时间序插入 count 条带 message_id 的观众行，message_id 即断言用的窗口锚点。"""
    for i in range(start_index, start_index + count):
        await store.chat.insert_live_chat(
            live_session_id=session_id,
            timestamp_ms=1000 + i,
            sender_role="viewer",
            sender_name="观众",
            content=f"消息{i}",
            message_type="danmaku",
            message_id=f"m{i}",
        )


@pytest.mark.asyncio
async def test_history_window_grows_append_only_before_limit(store: SQLiteDatabase) -> None:
    """未满窗时全量返回且只追加：旧消息跨读逐字稳定。"""
    agent = _make_agent(store, _FakeSessionManager(1))
    await _insert_turns(store, 3, start_index=1)
    first = await agent._read_history()
    assert [t.message_id for t in first] == ["m1", "m2", "m3"]

    await _insert_turns(store, 2, start_index=4)
    second = await agent._read_history()
    assert [t.message_id for t in second] == ["m1", "m2", "m3", "m4", "m5"]


@pytest.mark.asyncio
async def test_history_keeps_early_messages_after_many_new_turns(store: SQLiteDatabase) -> None:
    """读取跨过旧条数限制后，最初指令仍在场次前缀中。"""
    agent = _make_agent(store, _FakeSessionManager(1))
    await _insert_turns(store, 45, start_index=1)
    first = await agent._read_history()
    assert [turn.message_id for turn in first] == [f"m{i}" for i in range(1, 46)]
    await _insert_turns(store, 3, start_index=46)
    second = await agent._read_history()
    assert [turn.message_id for turn in second] == [f"m{i}" for i in range(1, 49)]
    assert second[:45] == first


@pytest.mark.asyncio
async def test_history_window_reset_on_session_switch(store: SQLiteDatabase) -> None:
    """场次切换（新开播）：旧场次锚点作废，新场次从空窗重新生长。"""
    session = _FakeSessionManager(1)
    agent = _make_agent(store, session)
    await _insert_turns(store, 6, start_index=1)
    await agent._read_history()

    session._pk = 2
    assert await agent._read_history() == []

    await _insert_turns(store, 2, start_index=101, session_id=2)
    fresh = await agent._read_history()
    assert [t.message_id for t in fresh] == ["m101", "m102"]


# ---------------------------------------------------------------------------
# 摘要链路复活：live_chat viewer 行 → topic_summary
# ---------------------------------------------------------------------------


def _make_maintainer(
    store: SQLiteDatabase,
    *,
    prompt_manager: PromptManager | None = None,
) -> tuple[BackgroundMaintainer, MagicMock]:
    room_state = RoomState()
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="观众在聊新版本更新"))
    memory = MagicMock()
    memory.ingest = AsyncMock(return_value=None)
    # 不触达摘要渲染路径时，传满足 render() -> str 的最小 fake；调用方若需
    # 真实渲染可显式传入 ``PromptManager(auto_scan_src=True).load_all()``
    if prompt_manager is None:
        prompt_manager = MagicMock()
        prompt_manager.render = MagicMock(return_value="PROMPT")
    maintainer = BackgroundMaintainer(
        {"enabled": True},
        room_state=room_state,
        llm_service=llm,
        session_manager=_FakeSessionManager(1),
        memory=memory,
        chat_repo=store.chat,
        prompt_manager=prompt_manager,
    )
    return maintainer, llm


@pytest.mark.asyncio
async def test_summarize_topic_reads_live_chat_viewer_rows(store: SQLiteDatabase) -> None:
    """seed live_chat viewer 行 → 摘要 LLM 收到弹幕文本，topic_summary 被写入。"""
    # 真实 PromptManager：完整摘要路径会调 manager.render("summary_system")，需模板被加载
    real_prompt_manager = PromptManager(auto_scan_src=True)
    real_prompt_manager.load_all()
    maintainer, llm = _make_maintainer(store, prompt_manager=real_prompt_manager)
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=100,
        sender_role="viewer",
        sender_name="观众A",
        content="新版本什么时候上线",
        message_type="danmaku",
    )

    await maintainer._summarize_topic(now_ms=200_000)

    # LLM 输入含观众弹幕（摘要输入不再恒空）
    prompt_text = llm.generate.await_args.args[0]
    assert "新版本什么时候上线" in prompt_text
    # topic_summary 写入房间态势（下游 ProactiveTrigger 的 topic 门据此放行）
    snap = maintainer._room_state.get_snapshot(now_ms=300_000)
    assert snap.topic_summary == "观众在聊新版本更新"


@pytest.mark.asyncio
async def test_summarize_topic_no_active_session_is_noop(store: SQLiteDatabase) -> None:
    """无显式场次 → 静默跳过（不调 LLM、不写摘要）。"""
    room_state = RoomState()
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="x"))
    # 早返回路径不走 render：满足 render() -> str 的最小 fake 即可
    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")
    maintainer = BackgroundMaintainer(
        {"enabled": True},
        room_state=room_state,
        llm_service=llm,
        session_manager=_FakeSessionManager(None),
        chat_repo=store.chat,
        prompt_manager=prompt_manager,
    )

    await maintainer._summarize_topic(now_ms=200_000)

    llm.generate.assert_not_awaited()
    assert room_state.get_snapshot(now_ms=300_000).topic_summary == ""


@pytest.mark.asyncio
async def test_summarize_topic_only_assistant_rows_clears_summary(store: SQLiteDatabase) -> None:
    """窗口内只有主播发言（无观众弹幕）→ 清空 topic_summary 防自嗨循环。"""
    maintainer, llm = _make_maintainer(store)
    maintainer._room_state.set_topic_summary("旧话题", now_ms=1)
    await store.chat.insert_live_chat(
        live_session_id=1,
        timestamp_ms=100,
        sender_role="assistant",
        sender_name="主播",
        content="大家好",
        message_type="speak",
    )

    await maintainer._summarize_topic(now_ms=200_000)

    llm.generate.assert_not_awaited()
    snap = maintainer._room_state.get_snapshot(now_ms=300_000)
    assert snap.topic_summary == ""
