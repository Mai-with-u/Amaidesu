"""v25 → v26 迁移：游戏事件、承诺与场次档案加存档列，存量行留空串（存档不明）。

用 v25 形状的最小 DDL 造前置形状后直接调用 v26 的 ``migrate(conn)``。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations.v26_game_save_columns import migrate as v26_migrate

# v25 形状的最小 DDL：三张表都还没有存档列
_V25_SCHEMA = """
CREATE TABLE game_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    game             TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    message          TEXT NOT NULL,
    scene            TEXT,
    timestamp_ms     INTEGER NOT NULL
);
CREATE TABLE commitments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    what               TEXT NOT NULL,
    game               TEXT,
    created_at_ms      INTEGER NOT NULL,
    updated_at_ms      INTEGER NOT NULL
);
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL
);
"""


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_v25_database_gains_save_columns_with_unknown_save() -> None:
    """存量行的存档为空串（不明）、档案行的游戏为空；再跑一次不多加列、不动已写内容。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_V25_SCHEMA)
        conn.execute(
            "INSERT INTO game_events(live_session_id, game, event_type, message, timestamp_ms)"
            " VALUES (1, 'minecraft', 'milestone', '屋子盖好了', 1000)"
        )
        conn.execute(
            "INSERT INTO commitments(what, game, created_at_ms, updated_at_ms) VALUES ('封屋顶', 'minecraft', 1, 1)"
        )
        conn.execute(
            "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id)"
            " VALUES (1, 'Minecraft：盖了屋子', '盖好啦', 7)"
        )

        v26_migrate(conn)

        event = conn.execute("SELECT save_id, save_name FROM game_events").fetchone()
        assert (event["save_id"], event["save_name"]) == ("", "")
        commitment = conn.execute("SELECT save_id, save_name FROM commitments").fetchone()
        assert (commitment["save_id"], commitment["save_name"]) == ("", "")
        line = conn.execute("SELECT game, save_id, save_name FROM archive_lines").fetchone()
        assert (line["game"], line["save_id"], line["save_name"]) == (None, "", "")

        conn.execute("UPDATE commitments SET save_id='ab12', save_name='新的世界'")
        v26_migrate(conn)  # 幂等：列已存在直接跳过，已写内容不被覆盖

        assert conn.execute("SELECT save_id FROM commitments").fetchone()[0] == "ab12"
        for table in ("game_events", "commitments", "archive_lines"):
            assert _columns(conn, table).count("save_id") == 1
        assert _columns(conn, "archive_lines").count("game") == 1
    finally:
        conn.close()


def test_migrate_skips_missing_tables() -> None:
    """没有这几张表的库什么都不做。"""
    conn = sqlite3.connect(":memory:")
    try:
        v26_migrate(conn)
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
    finally:
        conn.close()
