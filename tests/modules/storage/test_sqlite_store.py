"""SQLiteDatabase 与域仓储：建库自检、明细写入、聊天回灌查询、观众统计 upsert"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.modules.storage import SCHEMA_VERSION, SQLiteConnectionManager, list_expected_tables
from src.modules.storage.database import SQLiteDatabase
from tests.modules.storage.viewer_seed import seed_gift, seed_message, seed_replied, seed_viewer


async def _columns(store: SQLiteDatabase, table: str) -> dict:
    return {row["name"]: row for row in await store.execute(f"PRAGMA table_info({table})")}


async def test_fresh_store_passes_self_checks(store: SQLiteDatabase) -> None:
    await store.initialize()  # 幂等
    assert set(list_expected_tables()) <= set(await store.list_tables())
    assert await store.get_schema_version() == SCHEMA_VERSION
    await store.assert_schema_ready()
    assert await store.is_healthy() is True
    # simulated 贯穿列默认 0（真实源）
    for table in ("live_chat", "gifts", "super_chats"):
        assert int((await _columns(store, table))["simulated"]["dflt_value"]) == 0
    # 时间字段统一 *_ms 命名
    assert {"started_at_ms", "updated_at_ms", "ended_at_ms"} <= set(await _columns(store, "live_sessions"))
    assert "duration_ms" in await _columns(store, "topics")


def test_connection_manager_enables_wal_and_foreign_keys(db_path: Path) -> None:
    manager = SQLiteConnectionManager(db_path)
    conn = manager.connection()
    try:
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    finally:
        manager.close_all()


async def test_insert_methods_write_rows_and_simulated_flag(store: SQLiteDatabase) -> None:
    for simulated in (False, True):
        tag = "假" if simulated else "真"
        rowid = await store.chat.insert_live_chat(
            live_session_id=1,
            timestamp_ms=1,
            sender_role="viewer",
            content=tag,
            message_type="danmaku",
            simulated=simulated,
        )
        assert rowid > 0
        await store.chat.insert_gift(
            live_session_id=1,
            timestamp_ms=2,
            user_id="u",
            user_name="大佬",
            gift_name=tag,
            quantity=10,
            simulated=simulated,
        )
        await store.chat.insert_super_chat(
            live_session_id=1,
            timestamp_ms=3,
            user_id="u",
            user_name="SC哥",
            total_price=88_500,
            currency="bilibili_gold_coin",
            message=tag,
            simulated=simulated,
        )

    chat = await store.execute("SELECT * FROM live_chat ORDER BY id")
    assert [(r["content"], r["simulated"]) for r in chat] == [("真", 0), ("假", 1)]
    # 可选列缺省落 NULL
    assert (chat[0]["sender_id"], chat[0]["sender_name"]) == (None, None)
    gift = (await store.execute("SELECT * FROM gifts WHERE simulated=0"))[0]
    assert (gift["gift_name"], gift["quantity"], gift["user_name"]) == ("真", 10, "大佬")
    sc = await store.execute("SELECT * FROM super_chats WHERE simulated=0")
    assert [(r["message"], r["total_price"]) for r in sc] == [("真", 88_500)]


async def _seed_chat(store: SQLiteDatabase, live_session_id: int, items: list[tuple[int, str]]) -> None:
    """塞一批 ``(timestamp_ms, sender_id)`` 弹幕，内容为 ``<sender>@<ts>``。"""
    for ts, sid in items:
        await store.chat.insert_live_chat(
            live_session_id=live_session_id,
            timestamp_ms=ts,
            sender_role="viewer",
            sender_id=sid,
            sender_name=sid,
            content=f"{sid}@{ts}",
            message_type="danmaku",
        )


async def test_recent_live_chat_takes_latest_window_in_chronological_order(store: SQLiteDatabase) -> None:
    """先取最新窗口再反转为正序；直接 ASC LIMIT 会取到最老的几条。"""
    # 乱序到达
    await _seed_chat(store, 1, [(3000, "u1"), (1000, "u1"), (5000, "u2"), (2000, "u1"), (4000, "u2")])
    await _seed_chat(store, 2, [(2500, "u3")])

    def stamps(rows) -> list[int]:
        return [r["timestamp_ms"] for r in rows]

    assert stamps(await store.chat.list_recent_live_chat(live_session_id=1, limit=3)) == [3000, 4000, 5000]
    window = await store.chat.list_recent_live_chat(live_session_id=1, limit=10, before_timestamp_ms=4000)
    assert stamps(window) == [1000, 2000, 3000]
    window = await store.chat.list_recent_live_chat(live_session_id=1, limit=2, before_timestamp_ms=5000)
    assert stamps(window) == [3000, 4000]
    assert stamps(await store.chat.list_recent_live_chat(live_session_id=2)) == [2500]

    by_user = store.chat.list_messages_by_user
    assert stamps(await by_user(user_id="u1", since_timestamp_ms=0)) == [1000, 2000, 3000]
    assert stamps(await by_user(user_id="u1", since_timestamp_ms=1500)) == [2000, 3000]
    assert await by_user(user_id="nobody", since_timestamp_ms=0) == []


async def test_viewer_counters_accumulate_by_viewer_id(store: SQLiteDatabase) -> None:
    viewers = store.viewers
    vid = await seed_message(viewers, platform="bilibili", user_id="u1", user_name="张三", timestamp_ms=1000)
    # 同一账号 ID 换了昵称：仍是同一位观众，昵称更新
    assert (
        await seed_message(viewers, platform="bilibili", user_id="u1", user_name="张三（新昵称）", timestamp_ms=2000)
        == vid
    )
    await seed_gift(viewers, platform="bilibili", user_id="u1", user_name="张三（新昵称）", timestamp_ms=3000)
    await seed_replied(viewers, platform="bilibili", user_id="u1", timestamp_ms=4000)

    row = await viewers.get_viewer_stats(platform="bilibili", user_id="u1")
    counters = ("message_count", "gift_count", "replied_count", "interaction_count", "last_active_ms")
    assert tuple(row[k] for k in counters) == (2, 1, 1, 4, 4000)
    assert (row["id"], row["user_name"], row["account_id"], row["user_id"]) == (vid, "张三（新昵称）", "u1", "u1")

    # 付费即互动只在第一笔互动时记 1：已发过言的观众付费不重复计互动
    await viewers.record_paid(viewer_id=vid, amount=1000, timestamp_ms=5000)
    row = await viewers.get_viewer_stats(platform="bilibili", user_id="u1")
    assert (row["paid_count"], row["paid_amount"], row["interaction_count"]) == (1, 1000, 4)

    # 未建档的外部 ID：只查不建
    assert await viewers.find_viewer_id(platform="bilibili", external_id="nobody") is None
    assert await viewers.get_viewer_stats(platform="bilibili", user_id="nobody") is None


async def test_list_viewer_stats_ordering_paging_and_whitelist(store: SQLiteDatabase) -> None:
    viewers = store.viewers
    for ts in (1, 2):
        await seed_message(viewers, platform="bilibili", user_id="u_msg", user_name="m", timestamp_ms=ts)
    for ts in (3, 4, 5):
        await seed_gift(viewers, platform="bilibili", user_id="u_gift", user_name="g", timestamp_ms=ts)
    await seed_viewer(viewers, platform="bilibili", user_id="u_repl", user_name="r", timestamp_ms=5)
    for ts in (6, 7):
        await seed_replied(viewers, platform="bilibili", user_id="u_repl", timestamp_ms=ts)

    rows, total = await viewers.list_viewer_stats()
    assert (rows[0]["user_id"], len(rows), total) == ("u_msg", 3, 3)  # 默认按 message_count
    for order_by, top in (("gift_count", "u_gift"), ("last_active_ms", "u_repl"), ("replied_count", "u_repl")):
        assert (await viewers.list_viewer_stats(order_by=order_by))[0][0]["user_id"] == top

    rows, total = await viewers.list_viewer_stats(platform="bilibili", limit=2)
    assert (len(rows), total) == (2, 3)  # total 不随分页变化
    assert len((await viewers.list_viewer_stats(platform="bilibili", limit=2, offset=2))[0]) == 1

    for bad in ("user_name", "1; DROP TABLE viewers--", ""):
        with pytest.raises(ValueError):
            await viewers.list_viewer_stats(order_by=bad)
