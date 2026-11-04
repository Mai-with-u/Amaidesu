"""v17 → v18 迁移：观众表改为一行一人、外部 ID 分列，明细挂上观众 ID，画像改按观众 ID 存，昵称记录回填。

直接用 v17 形状的最小 DDL 造前置形状后调用 v18 的 ``migrate(conn)``：最新库的建表
DDL 已不含 ``viewer_profiles``（v25 已删），"先建最新库再退回旧形状"的做法不再成立。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v18_viewer_identity_mapping import migrate as v18_migrate

OPEN_ID = "39b8fedb-60a5-4e29-ac75-b16955f7e632"

# v17 形状的最小 DDL：v18 回调读写到的列都在里面，其余列与本回调无关。
_V17_SCHEMA = """
CREATE TABLE viewers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL,
    user_name TEXT NOT NULL, message_count INTEGER NOT NULL DEFAULT 0, gift_count INTEGER NOT NULL DEFAULT 0,
    replied_count INTEGER NOT NULL DEFAULT 0, interaction_count INTEGER NOT NULL DEFAULT 0,
    paid_count INTEGER NOT NULL DEFAULT 0, paid_amount INTEGER NOT NULL DEFAULT 0,
    last_active_ms INTEGER NOT NULL, UNIQUE(platform, user_id)
);
CREATE TABLE viewer_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, user_id TEXT NOT NULL,
    profile_text TEXT NOT NULL, last_compressed_at_ms INTEGER NOT NULL DEFAULT 0,
    updated_at_ms INTEGER NOT NULL, UNIQUE(platform, user_id)
);
CREATE TABLE viewer_names (
    id INTEGER PRIMARY KEY AUTOINCREMENT, viewer_id INTEGER NOT NULL, user_name TEXT NOT NULL,
    first_seen_ms INTEGER NOT NULL, last_seen_ms INTEGER NOT NULL, UNIQUE(viewer_id, user_name)
);
CREATE TABLE live_chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '', sender_role TEXT NOT NULL, sender_id TEXT, sender_name TEXT,
    simulated INTEGER NOT NULL DEFAULT 0, content TEXT NOT NULL, message_type TEXT NOT NULL
);
CREATE TABLE gifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL, user_name TEXT NOT NULL,
    gift_name TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, simulated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE super_chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL, user_name TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '', simulated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE guards (
    id INTEGER PRIMARY KEY AUTOINCREMENT, live_session_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL, user_name TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0
);
"""


def _make_v17() -> sqlite3.Connection:
    """v17 形状旧库 + 存量数据：同一 uid 有带/漏平台的重复行，开放平台 ID、兜底身份、模拟观众各一条。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_V17_SCHEMA)
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
    return conn


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
        for table in ("viewers", "viewer_names", "viewer_profiles", "live_chat", "gifts")
    }


def test_v17_viewers_split_ids_and_backfill() -> None:
    conn = _make_v17()
    try:
        v18_migrate(conn)

        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM viewers")}
        assert sorted(rows) == [1, 3, 4, 5, 6]  # 漏写平台的同一个 uid 并进带平台的那一行
        laowang = rows[1]
        assert (laowang["platform"], laowang["account_id"], laowang["open_id"]) == ("bilibili", "1001", None)
        assert (laowang["message_count"], laowang["last_active_ms"], laowang["name_seen_ms"]) == (7, 3_000, 3_000)
        assert (rows[3]["account_id"], rows[3]["open_id"], rows[3]["open_app_id"]) == (None, OPEN_ID, "")
        assert (rows[4]["platform"], rows[4]["account_id"], rows[4]["open_id"]) == ("bilibili", None, None)
        assert (rows[5]["platform"], rows[5]["account_id"]) == ("console", "console_user")
        assert [r["id"] for r in rows.values() if r["simulated"]] == [6]  # 明细全是模拟数据才算模拟观众
        assert {r["link_status"] for r in rows.values()} == {"confirmed"}

        chat = conn.execute("SELECT sender_id, platform, viewer_id, id_kind FROM live_chat ORDER BY id").fetchall()
        assert [(r["viewer_id"], r["id_kind"]) for r in chat] == [
            (1, "account"),
            (1, "account"),  # 漏写平台的旧弹幕也归到合并后的观众
            (3, "open"),
            (4, ""),  # 昵称兜底身份没有 ID 种类
            (6, "account"),
            (None, ""),  # 主播发言不挂观众
        ]
        (gift,) = conn.execute("SELECT viewer_id, id_kind FROM gifts").fetchall()
        assert (gift["viewer_id"], gift["id_kind"]) == (1, "account")

        # 冻结在 v18 里的建表语句能独立重建 viewer_profiles；对不上观众的画像丢弃，同人多份留较新的
        profile_columns = {row[1] for row in conn.execute("PRAGMA table_info(viewer_profiles)")}
        assert "viewer_id" in profile_columns and "user_id" not in profile_columns
        profiles = conn.execute("SELECT viewer_id, profile_text FROM viewer_profiles").fetchall()
        assert [(r["viewer_id"], r["profile_text"]) for r in profiles] == [(1, "画像B")]

        names = conn.execute(
            "SELECT user_name, first_seen_ms, last_seen_ms FROM viewer_names WHERE viewer_id=1 ORDER BY first_seen_ms"
        ).fetchall()
        assert [tuple(r) for r in names] == [("老王旧名", 100, 100), ("老王", 900, 2_000)]

        before = _snapshot(conn)
        v18_migrate(conn)  # 已是最新形状：不重复迁移
        assert _snapshot(conn) == before
    finally:
        conn.close()


async def test_new_database_has_v18_shape(db_path: Path) -> None:
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        viewer_cols = {r["name"] for r in await db.execute("PRAGMA table_info(viewers)")}
        assert {"account_id", "open_id", "open_app_id", "name_seen_ms", "link_status"} <= viewer_cols
        assert "user_id" not in viewer_cols
        for table in ("live_chat", "gifts", "super_chats", "guards"):
            columns = {r["name"] for r in await db.execute(f"PRAGMA table_info({table})")}
            assert {"viewer_id", "id_kind"} <= columns
    finally:
        await db.close()
