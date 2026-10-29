"""SQLiteDatabase 与域仓储：建库自检、明细写入、聊天回灌查询、观众统计 upsert"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict

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


def test_close_all_leaves_in_flight_transaction_to_finish_on_its_own_thread(db_path: Path) -> None:
    """关库请求撞上别的线程正在进行的事务：该连接不被关库方关掉，由持有线程收尾后自行关闭。

    复现的是停机时后台写入（流程单快照写穿）还在线程池里执行、关库线程同时关掉全部连接
    导致的进程级访问违例。用两个事件把时序钉死：工作线程写完第一句后停在事务中间，
    关库请求发出后再放行，继续写第二句（含嵌套事务）——关库方若直接关掉它的连接，
    第二句就会撞上已关闭的连接。
    """
    manager = SQLiteConnectionManager(db_path)
    with manager.transaction() as conn:
        conn.execute("CREATE TABLE t (v INTEGER)")

    in_flight = threading.Event()
    close_requested = threading.Event()
    outcome: Dict[str, Any] = {}

    def write_across_close() -> None:
        # 写一句 -> 停在事务中间等关库请求 -> 嵌套事务里再写一句 -> 提交
        try:
            with manager.transaction() as conn:
                outcome["conn"] = conn
                conn.execute("INSERT INTO t VALUES (1)")
                in_flight.set()
                close_requested.wait()
                with manager.transaction() as nested:
                    nested.execute("INSERT INTO t VALUES (2)")
            outcome["finished"] = True
        except Exception as exc:  # noqa: BLE001 失败原因交给主线程断言
            outcome["error"] = exc
        finally:
            in_flight.set()

    worker = threading.Thread(target=write_across_close)
    worker.start()
    try:
        # 超时只防挂死：正常路径工作线程写完第一句就会置位
        assert in_flight.wait(timeout=10)
        manager.close_all()
    finally:
        close_requested.set()
        worker.join()

    assert outcome.get("error") is None
    assert outcome.get("finished") is True
    # 事务收尾后连接已由工作线程自己关闭，管理器不再持有任何连接，新事务一律拒绝
    with pytest.raises(sqlite3.ProgrammingError):
        outcome["conn"].execute("SELECT 1")
    assert manager.connection_count == 0
    with pytest.raises(RuntimeError):
        with manager.transaction():
            pass
    # 关库前已开始的事务完整落盘
    probe = sqlite3.connect(str(db_path))
    try:
        assert probe.execute("SELECT v FROM t ORDER BY v").fetchall() == [(1,), (2,)]
    finally:
        probe.close()


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
        return [r.timestamp_ms for r in rows]

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

    row = await viewers.get_viewer(viewer_id=vid)
    counters = ("message_count", "gift_count", "replied_count", "interaction_count", "last_active_ms")
    assert tuple(getattr(row, k) for k in counters) == (2, 1, 1, 4, 4000)
    assert (row.id, row.user_name, row.account_id, row.user_id) == (vid, "张三（新昵称）", "u1", "u1")

    # 付费即互动只在第一笔互动时记 1：已发过言的观众付费不重复计互动
    await viewers.record_paid(viewer_id=vid, amount=1000, timestamp_ms=5000)
    row = await viewers.get_viewer(viewer_id=vid)
    assert (row.paid_count, row.paid_amount, row.interaction_count) == (1, 1000, 4)

    # 未建档的外部 ID：只查不建
    assert await viewers.find_viewer_id(platform="bilibili", external_id="nobody") is None
    assert await viewers.get_viewer(viewer_id=vid + 100) is None


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
    assert (rows[0].user_id, len(rows), total) == ("u_msg", 3, 3)  # 默认按 message_count
    for order_by, top in (("gift_count", "u_gift"), ("last_active_ms", "u_repl"), ("replied_count", "u_repl")):
        assert (await viewers.list_viewer_stats(order_by=order_by))[0][0].user_id == top

    rows, total = await viewers.list_viewer_stats(platform="bilibili", limit=2)
    assert (len(rows), total) == (2, 3)  # total 不随分页变化
    assert len((await viewers.list_viewer_stats(platform="bilibili", limit=2, offset=2))[0]) == 1

    for bad in ("user_name", "1; DROP TABLE viewers--", ""):
        with pytest.raises(ValueError):
            await viewers.list_viewer_stats(order_by=bad)
