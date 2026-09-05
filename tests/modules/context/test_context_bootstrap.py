"""ContextService 启动回灌（live_chat → ContextService）测试。

覆盖：
- 正常路径：viewer + assistant 交错 → 聚合轮数正确，get_history 可见
- 空库路径：返回 0 不抛
- 纯 viewer 路径：尾部未闭合轮 → assistant_message=None
- session_id 字符串 → live_chat.live_session_id INTEGER 映射一致性

回灌函数定义于 ``main._bootstrap_context_from_live_chat``，由组合根在
启动时调用——本测试通过直接 import 触发验证，避免起 EventBus/LLM。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from main import _LIVE_SESSION_ID, _bootstrap_context_from_live_chat
from src.modules.context import ContextService
from src.modules.storage.sqlite_store import SQLiteStore
from src.modules.storage.storage_ledger import StorageLedger


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="bootstrap-ctx-"))
    yield td / "bootstrap.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
async def context_service() -> AsyncGenerator[ContextService, None]:
    svc = ContextService()
    await svc.initialize()
    yield svc
    await svc.cleanup()


@pytest.mark.asyncio
async def test_bootstrap_seeds_turns_from_live_chat(
    store: SQLiteStore, context_service: ContextService
) -> None:
    """viewer+assistant 交错 → 聚合为多轮，get_history 可见消息。"""
    pk = StorageLedger._session_pk_to_int(_LIVE_SESSION_ID)
    base = 1_700_000_000_000  # ms

    # 时间正序插入 6 条：viewer / assistant / viewer / viewer / assistant / viewer
    # 聚合预期 3 轮：[{v1}, {a1}] → [{v2,v3}, {a2}] → [{v4}] (未闭合)
    await store.insert_live_chat(
        live_session_id=pk, timestamp_ms=base + 100,
        sender_role="viewer", sender_id="u1", sender_name="U1",
        content="弹幕1", message_type="danmaku",
    )
    await store.insert_live_chat(
        live_session_id=pk, timestamp_ms=base + 200,
        sender_role="assistant", sender_name="主播",
        content="回复1", message_type="speak",
    )
    await store.insert_live_chat(
        live_session_id=pk, timestamp_ms=base + 300,
        sender_role="viewer", sender_id="u2", sender_name="U2",
        content="弹幕2", message_type="danmaku",
    )
    await store.insert_live_chat(
        live_session_id=pk, timestamp_ms=base + 400,
        sender_role="viewer", sender_id="u3", sender_name="U3",
        content="弹幕3", message_type="danmaku",
    )
    await store.insert_live_chat(
        live_session_id=pk, timestamp_ms=base + 500,
        sender_role="assistant", sender_name="主播",
        content="回复2", message_type="speak",
    )
    await store.insert_live_chat(
        live_session_id=pk, timestamp_ms=base + 600,
        sender_role="viewer", sender_id="u4", sender_name="U4",
        content="弹幕4", message_type="danmaku",
    )

    seeded = await _bootstrap_context_from_live_chat(context_service, store)

    # 聚合结果：第一轮 [v1, a1] / 第二轮 [v2,v3, a2] / 第三轮 [v4]（未闭合）
    assert seeded == 3

    history = await context_service.get_history(_LIVE_SESSION_ID)
    # 2 + 3 + 1 = 6 条消息（每个轮 viewer + assistant，最后轮只 viewer）
    assert len(history) == 6
    contents = [m.content for m in history]
    assert contents == ["弹幕1", "回复1", "弹幕2", "弹幕3", "回复2", "弹幕4"]


@pytest.mark.asyncio
async def test_bootstrap_empty_db_noop(
    store: SQLiteStore, context_service: ContextService
) -> None:
    """空库：返回 0，不抛错，且不污染 ContextService。"""
    seeded = await _bootstrap_context_from_live_chat(context_service, store)
    assert seeded == 0

    history = await context_service.get_history(_LIVE_SESSION_ID)
    assert history == []


@pytest.mark.asyncio
async def test_bootstrap_single_viewer_no_assistant(
    store: SQLiteStore, context_service: ContextService
) -> None:
    """纯 viewer 行：返回 1 轮（assistant_message=None）。"""
    pk = StorageLedger._session_pk_to_int(_LIVE_SESSION_ID)
    base = 1_700_000_000_000

    await store.insert_live_chat(
        live_session_id=pk, timestamp_ms=base + 100,
        sender_role="viewer", sender_id="u1", sender_name="U1",
        content="未回复的弹幕", message_type="danmaku",
    )

    seeded = await _bootstrap_context_from_live_chat(context_service, store)
    assert seeded == 1

    history = await context_service.get_history(_LIVE_SESSION_ID)
    assert len(history) == 1
    assert history[0].content == "未回复的弹幕"

    # 通过配对视图确认尾部未闭合轮
    turns = await context_service.get_dialogue_history(_LIVE_SESSION_ID)
    assert len(turns) == 1
    assert turns[0].viewer_messages == ["未回复的弹幕"]
    assert turns[0].assistant_message is None


def test_session_pk_consistency() -> None:
    """session_id → INTEGER pk 映射必须稳定（同源同 pk）。

    写入路径（StorageLedger._session_pk）和回灌查询路径（list_recent_live_chat
    入参）共用 _session_pk_to_int；任何一方换算法都会查不到同场数据。
    """
    pk_a = StorageLedger._session_pk_to_int(_LIVE_SESSION_ID)
    pk_b = StorageLedger._session_pk_to_int(_LIVE_SESSION_ID)
    assert pk_a == pk_b
    # 不同 session_id 必须不同 pk（防撞）
    assert pk_a != StorageLedger._session_pk_to_int("other_session")
    # 32 位无符号整数范围（MD5 前 8 hex）
    assert 0 <= pk_a < 2**32
