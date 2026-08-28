"""
SQLiteStore 领域写入方法单测（v2.0.5 / ADR-006 溯源链收口）

覆盖：
- insert_live_chat / insert_gift / insert_super_chat 三方法行可落
- simulated 端到端：实参 bool → 列 INTEGER 0/1
- 排除查询 ``WHERE simulated=0`` 不含 simulated=1 行
- lastrowid 返回正整数
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.storage.sqlite_store import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="w3-storage-insert-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


# ===== insert_live_chat =====


@pytest.mark.asyncio
async def test_insert_live_chat_basic(store: SQLiteStore) -> None:
    rowid = await store.insert_live_chat(
        live_session_id=1,
        timestamp_ms=1_700_000_000_000,
        sender_role="viewer",
        sender_id="u_001",
        sender_name="观众A",
        content="主播好可爱",
        message_type="danmaku",
        simulated=False,
    )
    assert rowid > 0, "lastrowid 应为正整数"
    rows = await store.execute("SELECT * FROM live_chat WHERE id=?", (rowid,))
    assert len(rows) == 1
    row = rows[0]
    assert str(row["content"]) == "主播好可爱"
    assert str(row["message_type"]) == "danmaku"
    assert str(row["sender_name"]) == "观众A"
    assert int(row["simulated"]) == 0


@pytest.mark.asyncio
async def test_insert_live_chat_simulated_true(store: SQLiteStore) -> None:
    rowid = await store.insert_live_chat(
        live_session_id=1,
        timestamp_ms=1_700_000_000_001,
        sender_role="viewer",
        content="模拟弹幕",
        message_type="danmaku",
        simulated=True,
    )
    rows = await store.execute("SELECT simulated FROM live_chat WHERE id=?", (rowid,))
    assert int(rows[0]["simulated"]) == 1


@pytest.mark.asyncio
async def test_insert_live_chat_optionals_default(store: SQLiteStore) -> None:
    """sender_id/sender_name/tool_result 缺省应能落库（NULL/默认值），不报错。"""
    rowid = await store.insert_live_chat(
        live_session_id=2,
        timestamp_ms=1_700_000_000_010,
        sender_role="tool",
        content="no sender",
        message_type="danmaku",
    )
    rows = await store.execute("SELECT sender_id, sender_name, tool_result FROM live_chat WHERE id=?", (rowid,))
    row = rows[0]
    assert row["sender_id"] is None
    assert row["sender_name"] is None
    assert row["tool_result"] is None


# ===== insert_gift =====


@pytest.mark.asyncio
async def test_insert_gift_basic(store: SQLiteStore) -> None:
    rowid = await store.insert_gift(
        live_session_id=3,
        timestamp_ms=1_700_000_000_100,
        user_id="u_777",
        user_name="大佬",
        gift_name="小星星",
        gift_count=10,
        simulated=False,
    )
    rows = await store.execute("SELECT * FROM gifts WHERE id=?", (rowid,))
    row = rows[0]
    assert str(row["user_name"]) == "大佬"
    assert str(row["gift_name"]) == "小星星"
    assert int(row["gift_count"]) == 10
    assert int(row["simulated"]) == 0


@pytest.mark.asyncio
async def test_insert_gift_simulated_true(store: SQLiteStore) -> None:
    rowid = await store.insert_gift(
        live_session_id=3,
        timestamp_ms=1_700_000_000_101,
        user_id="u_sim",
        user_name="模拟观众",
        gift_name="小星星",
        gift_count=1,
        simulated=True,
    )
    rows = await store.execute("SELECT simulated FROM gifts WHERE id=?", (rowid,))
    assert int(rows[0]["simulated"]) == 1


# ===== insert_super_chat =====


@pytest.mark.asyncio
async def test_insert_super_chat_basic(store: SQLiteStore) -> None:
    rowid = await store.insert_super_chat(
        live_session_id=4,
        timestamp_ms=1_700_000_001_000,
        user_id="u_sc_001",
        user_name="SC哥",
        amount=88.5,
        message="辛苦主播",
        simulated=False,
    )
    rows = await store.execute("SELECT * FROM super_chats WHERE id=?", (rowid,))
    row = rows[0]
    assert str(row["user_name"]) == "SC哥"
    assert float(row["amount"]) == 88.5
    assert str(row["message"]) == "辛苦主播"
    assert int(row["simulated"]) == 0


@pytest.mark.asyncio
async def test_insert_super_chat_simulated_true(store: SQLiteStore) -> None:
    rowid = await store.insert_super_chat(
        live_session_id=4,
        timestamp_ms=1_700_000_001_001,
        user_id="u_sc_sim",
        user_name="模拟SC",
        amount=10.0,
        message="模拟SC消息",
        simulated=True,
    )
    rows = await store.execute("SELECT simulated FROM super_chats WHERE id=?", (rowid,))
    assert int(rows[0]["simulated"]) == 1


# ===== 端到端：写入 + 排除查询 =====


@pytest.mark.asyncio
async def test_where_simulated_zero_excludes_simulated_rows(store: SQLiteStore) -> None:
    """消费者统计查询 WHERE simulated=0 应只返回真实源数据。"""
    # live_chat
    await store.insert_live_chat(
        live_session_id=10, timestamp_ms=1, sender_role="viewer", content="真", message_type="danmaku"
    )
    await store.insert_live_chat(
        live_session_id=10, timestamp_ms=2, sender_role="viewer", content="假", message_type="danmaku", simulated=True
    )
    # gifts
    await store.insert_gift(
        live_session_id=10, timestamp_ms=10, user_id="u", user_name="n", gift_name="g", gift_count=1
    )
    await store.insert_gift(
        live_session_id=10, timestamp_ms=11, user_id="u", user_name="n", gift_name="g", gift_count=1, simulated=True
    )
    # super_chats
    await store.insert_super_chat(
        live_session_id=10, timestamp_ms=20, user_id="u", user_name="n", amount=1, message="真"
    )
    await store.insert_super_chat(
        live_session_id=10, timestamp_ms=21, user_id="u", user_name="n", amount=1, message="假", simulated=True
    )

    real_chat = await store.execute("SELECT * FROM live_chat WHERE simulated=0")
    assert len(real_chat) == 1 and str(real_chat[0]["content"]) == "真"
    all_chat = await store.execute("SELECT * FROM live_chat")
    assert len(all_chat) == 2

    real_gifts = await store.execute("SELECT * FROM gifts WHERE simulated=0")
    assert len(real_gifts) == 1
    all_gifts = await store.execute("SELECT * FROM gifts")
    assert len(all_gifts) == 2

    real_sc = await store.execute("SELECT * FROM super_chats WHERE simulated=0")
    assert len(real_sc) == 1 and str(real_sc[0]["message"]) == "真"
    all_sc = await store.execute("SELECT * FROM super_chats")
    assert len(all_sc) == 2
