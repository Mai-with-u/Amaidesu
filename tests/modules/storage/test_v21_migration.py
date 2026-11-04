"""v20 → v21 迁移：``live_sessions`` 加本场备忘录全文列，存量场次补空串。

直接用 v20 形状的最小 DDL 造前置形状后调用 v21 的 ``migrate(conn)``：最新库已没有
notebook_text（v25 删除），不能再"先建最新库再退回旧形状"。列的最终退役由 v25
迁移测试覆盖，这里只覆盖 v21 回调补列的口径与幂等。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations.v21_live_session_notebook import migrate as v21_migrate

# v20 形状的最小 DDL：live_sessions 还没有 notebook_text 列
_V20_SCHEMA = """
CREATE TABLE live_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ms INTEGER NOT NULL,
    ended_at_ms   INTEGER,
    updated_at_ms INTEGER NOT NULL
);
"""


def test_v20_database_gains_empty_notebook_column() -> None:
    """升级后存量场次的备忘录为空串、新行默认也是空串；再跑一次不多加列、不改写已写内容。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_V20_SCHEMA)
        conn.execute(
            "INSERT INTO live_sessions(id, started_at_ms, ended_at_ms, updated_at_ms) VALUES (1, 1000, 2000, 2000)"
        )
        conn.execute("INSERT INTO live_sessions(id, started_at_ms, updated_at_ms) VALUES (2, 3000, 3000)")

        v21_migrate(conn)

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(live_sessions)")}
        assert "notebook_text" in columns
        assert [r["notebook_text"] for r in conn.execute("SELECT notebook_text FROM live_sessions ORDER BY id")] == [
            "",
            "",
        ]
        conn.execute("UPDATE live_sessions SET notebook_text=? WHERE id=2", ("本场结论",))
        conn.execute("INSERT INTO live_sessions(id, started_at_ms, updated_at_ms) VALUES (3, 4000, 4000)")
        assert conn.execute("SELECT notebook_text FROM live_sessions WHERE id=3").fetchone()[0] == ""

        v21_migrate(conn)  # 幂等：列已存在直接跳过，已有内容不被覆盖

        assert conn.execute("SELECT notebook_text FROM live_sessions WHERE id=2").fetchone()[0] == "本场结论"
        assert (
            len([row for row in conn.execute("PRAGMA table_info(live_sessions)") if row["name"] == "notebook_text"])
            == 1
        )
    finally:
        conn.close()


def test_migrate_is_idempotent_on_latest_shape() -> None:
    """列已存在时直接跳过；没有场次表的库什么都不做。"""
    conn = sqlite3.connect(":memory:")
    try:
        v21_migrate(conn)  # 没有表
        conn.execute("CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, notebook_text TEXT NOT NULL DEFAULT '')")
        v21_migrate(conn)
        v21_migrate(conn)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(live_sessions)")]
        assert columns.count("notebook_text") == 1
    finally:
        conn.close()
