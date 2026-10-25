"""v17 → v18 迁移：观众表改为一行一人、外部 ID 分列，明细挂上观众 ID，画像改按观众 ID 存，昵称记录回填。

先建最新库，再把观众相关表退回 v17 形状、写入存量数据，重新打开触发迁移。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.schema import SCHEMA_VERSION

OPEN_ID = "39b8fedb-60a5-4e29-ac75-b16955f7e632"

_V17_VIEWERS = """
CREATE TABLE viewers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL,
    user_name TEXT NOT NULL, message_count INTEGER NOT NULL DEFAULT 0, gift_count INTEGER NOT NULL DEFAULT 0,
    replied_count INTEGER NOT NULL DEFAULT 0, interaction_count INTEGER NOT NULL DEFAULT 0,
    paid_count INTEGER NOT NULL DEFAULT 0, paid_amount INTEGER NOT NULL DEFAULT 0,
    last_active_ms INTEGER NOT NULL, UNIQUE(platform, user_id)
)"""
_V17_PROFILES = """
CREATE TABLE viewer_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, user_id TEXT NOT NULL,
    profile_text TEXT NOT NULL, last_compressed_at_ms INTEGER NOT NULL DEFAULT 0,
    updated_at_ms INTEGER NOT NULL, UNIQUE(platform, user_id)
)"""


async def _open(path: Path) -> SQLiteDatabase:
    db = SQLiteDatabase(path)
    await db.initialize()
    return db


def _make_v17(path: Path) -> None:
    """把观众相关表退回 v17 形状并写入存量数据。"""
    conn = sqlite3.connect(path)
    for table in ("live_chat", "gifts", "super_chats", "guards"):
        conn.execute(f"DROP INDEX IF EXISTS idx_{table}_viewer")
        conn.execute(f"ALTER TABLE {table} DROP COLUMN viewer_id")
        conn.execute(f"ALTER TABLE {table} DROP COLUMN id_kind")
    conn.execute("DROP INDEX IF EXISTS idx_viewer_names_name")
    conn.execute("DROP TABLE viewer_names")
    conn.execute("DROP TABLE viewers")
    conn.execute("DROP TABLE viewer_profiles")
    conn.execute(_V17_VIEWERS)
    conn.execute(_V17_PROFILES)
    conn.execute("DELETE FROM schema_migrations WHERE version > 17")
    viewers = [
        # (id, platform, user_id, user_name, message_count, last_active_ms)
        (1, "bilibili", "1001", "老王", 5, 1_000),
        (2, "", "1001", "老王", 2, 3_000),  # 旧版采集器漏写平台的同一个 uid
        (3, "bilibili", OPEN_ID, "开放用户", 1, 500),
        (4, "", "bili_路人甲", "路人甲", 1, 400),  # 昵称兜底身份，没有真实 ID
        (5, "console", "console_user", "控制台", 3, 600),
        (6, "bilibili", "sim_veteran_01", "模拟老粉", 2, 700),
    ]
    conn.executemany(
        "INSERT INTO viewers(id, platform, user_id, user_name, message_count, last_active_ms) VALUES (?,?,?,?,?,?)",
        viewers,
    )
    chat = [
        # (platform, sender_role, sender_id, sender_name, ts, simulated)
        ("bilibili", "viewer", "1001", "老王旧名", 100, 0),
        ("", "viewer", "1001", "老王", 2_000, 0),
        ("bilibili", "viewer", OPEN_ID, "开放用户", 500, 0),
        ("", "viewer", "bili_路人甲", "路人甲", 400, 0),
        ("bilibili", "viewer", "sim_veteran_01", "模拟老粉", 700, 1),
        ("bilibili", "assistant", None, "主播", 800, 0),
    ]
    conn.executemany(
        "INSERT INTO live_chat(live_session_id, platform, sender_role, sender_id, sender_name, timestamp_ms,"
        " simulated, content, message_type) VALUES (1, ?, ?, ?, ?, ?, ?, '话', 'danmaku')",
        chat,
    )
    conn.execute(
        "INSERT INTO gifts(live_session_id, timestamp_ms, platform, user_id, user_name, gift_name, quantity)"
        " VALUES (1, 900, 'bilibili', '1001', '老王', '辣条', 1)"
    )
    conn.executemany(
        "INSERT INTO viewer_profiles(platform, user_id, profile_text, updated_at_ms) VALUES (?,?,?,?)",
        [("bilibili", "1001", "画像A", 10), ("", "1001", "画像B", 20), ("bilibili", "ghost", "无主画像", 30)],
    )
    conn.commit()
    conn.close()


async def _snapshot(db: SQLiteDatabase) -> Dict[str, Any]:
    return {
        table: [tuple(r) for r in await db.execute(f"SELECT * FROM {table} ORDER BY id")]
        for table in ("viewers", "viewer_names", "viewer_profiles", "live_chat", "gifts")
    }


async def test_v17_viewers_split_ids_and_backfill(db_path: Path) -> None:
    await (await _open(db_path)).close()  # 先建最新库，再退回 v17 形状
    _make_v17(db_path)

    db = await _open(db_path)
    try:
        assert await db.get_schema_version() == SCHEMA_VERSION
        rows = {r["id"]: r for r in await db.execute("SELECT * FROM viewers")}
        assert sorted(rows) == [1, 3, 4, 5, 6]  # 漏写平台的同一个 uid 并进带平台的那一行
        laowang = rows[1]
        assert (laowang["platform"], laowang["account_id"], laowang["open_id"]) == ("bilibili", "1001", None)
        assert (laowang["message_count"], laowang["last_active_ms"], laowang["name_seen_ms"]) == (7, 3_000, 3_000)
        assert (rows[3]["account_id"], rows[3]["open_id"], rows[3]["open_app_id"]) == (None, OPEN_ID, "")
        assert (rows[4]["platform"], rows[4]["account_id"], rows[4]["open_id"]) == ("bilibili", None, None)
        assert (rows[5]["platform"], rows[5]["account_id"]) == ("console", "console_user")
        assert [r["id"] for r in rows.values() if r["simulated"]] == [6]  # 明细全是模拟数据才算模拟观众
        assert {r["link_status"] for r in rows.values()} == {"confirmed"}

        chat = await db.execute("SELECT sender_id, platform, viewer_id, id_kind FROM live_chat ORDER BY id")
        assert [(r["viewer_id"], r["id_kind"]) for r in chat] == [
            (1, "account"),
            (1, "account"),  # 漏写平台的旧弹幕也归到合并后的观众
            (3, "open"),
            (4, ""),  # 昵称兜底身份没有 ID 种类
            (6, "account"),
            (None, ""),  # 主播发言不挂观众
        ]
        (gift,) = await db.execute("SELECT viewer_id, id_kind FROM gifts")
        assert (gift["viewer_id"], gift["id_kind"]) == (1, "account")

        profiles = await db.execute("SELECT viewer_id, profile_text FROM viewer_profiles")
        assert [(r["viewer_id"], r["profile_text"]) for r in profiles] == [(1, "画像B")]  # 保留较新的，无主画像丢弃

        names = await db.execute(
            "SELECT user_name, first_seen_ms, last_seen_ms FROM viewer_names WHERE viewer_id=1 ORDER BY first_seen_ms"
        )
        assert [tuple(r) for r in names] == [("老王旧名", 100, 100), ("老王", 900, 2_000)]

        before = await _snapshot(db)
    finally:
        await db.close()

    # 再次打开：已是最新版本，不重复迁移
    db = await _open(db_path)
    try:
        assert await _snapshot(db) == before
    finally:
        await db.close()


async def test_new_database_has_v18_shape(db_path: Path) -> None:
    await (await _open(db_path)).close()
    conn = sqlite3.connect(db_path)
    try:
        viewer_cols = {row[1] for row in conn.execute("PRAGMA table_info(viewers)")}
        assert {"account_id", "open_id", "open_app_id", "name_seen_ms", "link_status"} <= viewer_cols
        assert "user_id" not in viewer_cols
        assert "viewer_id" in {row[1] for row in conn.execute("PRAGMA table_info(viewer_profiles)")}
        for table in ("live_chat", "gifts", "super_chats", "guards"):
            assert {"viewer_id", "id_kind"} <= {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()
