"""付费观众查询：付费三表有无该观众记录、外部 ID → 观众 ID 的批量查询"""

from __future__ import annotations

from typing import AsyncIterator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.utils.time_utils import now_ms
from tests.modules.storage.viewer_seed import seed_message

PLATFORM = "bilibili"


@pytest.fixture
async def store(tmp_path) -> AsyncIterator[SQLiteDatabase]:
    db = SQLiteDatabase(tmp_path / "paid.db")
    await db.initialize()
    yield db
    await db.close()


async def _viewer(store: SQLiteDatabase, user_id: str, name: str) -> int:
    return await seed_message(store.viewers, platform=PLATFORM, user_id=user_id, user_name=name, timestamp_ms=now_ms())


async def _gift(store: SQLiteDatabase, viewer_id: int, user_id: str) -> None:
    await store.chat.insert_gift(
        live_session_id=1,
        timestamp_ms=now_ms(),
        platform=PLATFORM,
        user_id=user_id,
        user_name="礼官",
        viewer_id=viewer_id,
        gift_name="小心心",
        quantity=1,
        total_price=1000,
        currency="bilibili_gold_coin",
    )


async def test_payment_history_lists_viewers_with_any_paid_detail_row(store: SQLiteDatabase) -> None:
    """礼物 / SC / 上舰三表任一有该观众的行，就算付过费。"""
    paid = await _viewer(store, "u_paid", "付费观众")
    free = await _viewer(store, "u_free", "普通观众")
    await _gift(store, paid, "u_paid")

    found = await store.chat.list_viewers_with_payment_history(viewer_ids=[paid, free])

    assert found == [paid]


async def test_payment_history_accepts_empty_input_without_querying(store: SQLiteDatabase) -> None:
    assert await store.chat.list_viewers_with_payment_history(viewer_ids=[]) == []


async def test_payment_history_covers_super_chat_and_guard_tables(store: SQLiteDatabase) -> None:
    """SC 与上舰同样计入付过费。"""
    sc_viewer = await _viewer(store, "u_sc", "SC 观众")
    guard_viewer = await _viewer(store, "u_guard", "舰长")
    await store.chat.insert_super_chat(
        live_session_id=1,
        timestamp_ms=now_ms(),
        platform=PLATFORM,
        user_id="u_sc",
        user_name="SC 观众",
        viewer_id=sc_viewer,
        message="SC",
        total_price=5000,
        currency="bilibili_gold_coin",
    )
    await store.chat.insert_guard(
        live_session_id=1,
        timestamp_ms=now_ms(),
        platform=PLATFORM,
        user_id="u_guard",
        user_name="舰长",
        viewer_id=guard_viewer,
        guard_level=3,
        total_price=138000,
        currency="bilibili_gold_coin",
    )

    found = await store.chat.list_viewers_with_payment_history(viewer_ids=[sc_viewer, guard_viewer])

    assert sorted(found) == sorted([sc_viewer, guard_viewer])


async def test_find_viewer_ids_by_identity_keeps_id_kinds_apart(store: SQLiteDatabase) -> None:
    """账号 ID 与开放平台 ID 是两套编号：按 ID 种类分别查，不混着比。"""
    await seed_message(store.viewers, platform=PLATFORM, user_id="acc_1", user_name="账号观众", timestamp_ms=now_ms())

    account_hit = await store.viewers.find_viewer_ids_by_identity(
        platform=PLATFORM, id_kind="account", external_ids=["acc_1"]
    )
    open_hit = await store.viewers.find_viewer_ids_by_identity(
        platform=PLATFORM, id_kind="open", external_ids=["acc_1"]
    )

    assert len(account_hit) == 1
    assert open_hit == {}
