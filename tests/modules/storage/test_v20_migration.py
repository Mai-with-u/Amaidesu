"""v19 → v20 迁移：``session_recaps`` 加收播整理进度两列，存量回顾视为已整理。

直接用 v19 形状的最小 DDL 造前置形状后调用 v20 的 ``migrate(conn)``：最新库已没有
session_recaps（v25 删除），不能再"先建最新库再退回旧形状"。表的存在性由 v25
迁移测试覆盖，这里只覆盖 v20 回调对存量回顾的回填与幂等。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations.v20_session_recap_settlement import migrate as v20_migrate

# v19 形状的最小 DDL：session_recaps 还没有整理进度两列
_V19_SCHEMA = """
CREATE TABLE live_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL,
    ended_at_ms   INTEGER,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE session_recaps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id INTEGER NOT NULL UNIQUE,
    started_at_ms   INTEGER NOT NULL,
    ended_at_ms     INTEGER NOT NULL,
    recap_text      TEXT NOT NULL,
    created_at_ms   INTEGER NOT NULL
);
"""


def _make_v19() -> sqlite3.Connection:
    """场次 1、2 有回顾，场次 3 已结束但没回顾。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_V19_SCHEMA)
    for pk in (1, 2, 3):
        conn.execute(
            "INSERT INTO live_sessions(id, started_at_ms, ended_at_ms, updated_at_ms) VALUES (?, ?, ?, ?)",
            (pk, pk * 1_000, pk * 1_000 + 500, pk * 1_000 + 500),
        )
    for pk, created in ((1, 1_600), (2, 2_600)):
        conn.execute(
            "INSERT INTO session_recaps(live_session_id, started_at_ms, ended_at_ms, recap_text, created_at_ms)"
            " VALUES (?, ?, ?, '老回顾', ?)",
            (pk, pk * 1_000, pk * 1_000 + 500, created),
        )
    return conn


def _progress(conn: sqlite3.Connection) -> dict[int, tuple[int | None, int | None]]:
    return {
        int(r["live_session_id"]): (r["long_memory_merged_at_ms"], r["settled_at_ms"])
        for r in conn.execute("SELECT live_session_id, long_memory_merged_at_ms, settled_at_ms FROM session_recaps")
    }


def test_v19_database_treats_existing_recaps_as_settled() -> None:
    """v19 旧库升级：存量回顾两列填回顾写入时刻；没回顾的场次不凭空造行；再跑一次零变更。"""
    conn = _make_v19()
    try:
        v20_migrate(conn)

        assert _progress(conn) == {1: (1_600, 1_600), 2: (2_600, 2_600)}
        assert conn.execute("SELECT COUNT(*) FROM session_recaps WHERE live_session_id=3").fetchone()[0] == 0

        v20_migrate(conn)  # 列已存在：不再回填
        assert _progress(conn) == {1: (1_600, 1_600), 2: (2_600, 2_600)}
    finally:
        conn.close()


def test_rerun_keeps_new_recap_unsettled() -> None:
    """回填只在补列那一次执行：列已存在时重跑回调，不把新写的、没整理完的回顾误标成已整理。"""
    conn = _make_v19()
    try:
        v20_migrate(conn)
        conn.execute(
            "INSERT INTO session_recaps(live_session_id, started_at_ms, ended_at_ms, recap_text, created_at_ms)"
            " VALUES (9, 1, 2, '刚写的回顾', 3000)"
        )

        v20_migrate(conn)

        assert _progress(conn)[9] == (None, None)
    finally:
        conn.close()
