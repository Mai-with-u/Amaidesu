"""v26 → v27 迁移：场次档案加"出自哪一场""是不是长期有效的事"两列，存量行留空（滚动之前的旧行）。

用 v26 形状的最小 DDL 造前置形状后直接调用 v27 的 ``migrate(conn)``。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations.v27_rolling_archive import migrate as v27_migrate

# v26 形状的最小 DDL：档案表已有游戏与存档列，还没有滚动两列
_V26_SCHEMA = """
CREATE TABLE archive_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id  INTEGER NOT NULL,
    text             TEXT NOT NULL,
    quote            TEXT NOT NULL,
    evidence_chat_id INTEGER NOT NULL,
    game             TEXT,
    save_id          TEXT NOT NULL DEFAULT '',
    save_name        TEXT NOT NULL DEFAULT ''
);
"""


def _columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(archive_lines)")]


def test_v26_archive_gains_rolling_columns_and_old_rows_stay_legacy() -> None:
    """存量行出自哪一场为空（滚动之前的旧行）、不算长期；再跑一次不多加列、不动已写内容。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_V26_SCHEMA)
        conn.execute(
            "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id)"
            " VALUES (3, 'Minecraft：给女仆取了名', '就叫团子', 7)"
        )

        v27_migrate(conn)

        row = conn.execute("SELECT from_session_id, lasting FROM archive_lines").fetchone()
        assert (row["from_session_id"], row["lasting"]) == (None, 0)
        indexes = {item[1] for item in conn.execute("PRAGMA index_list(archive_lines)")}
        assert "idx_archive_lines_from_session" in indexes

        conn.execute("UPDATE archive_lines SET from_session_id=2, lasting=1")
        v27_migrate(conn)  # 幂等：列已存在直接跳过，已写内容不被覆盖

        row = conn.execute("SELECT from_session_id, lasting FROM archive_lines").fetchone()
        assert (row["from_session_id"], row["lasting"]) == (2, 1)
        assert _columns(conn).count("from_session_id") == 1
        assert _columns(conn).count("lasting") == 1
    finally:
        conn.close()


def test_migrate_skips_missing_table() -> None:
    """没有档案表的库什么都不做。"""
    conn = sqlite3.connect(":memory:")
    try:
        v27_migrate(conn)
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
    finally:
        conn.close()
