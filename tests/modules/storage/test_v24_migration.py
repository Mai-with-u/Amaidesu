"""v24 迁移测试：场次整理进度三列的回填与幂等，承诺与档案两张新表。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v24_memory_ledger import migrate as v24_migrate
from src.modules.storage.schema import SCHEMA_VERSION, list_expected_tables


def _make_v23_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE live_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_id       TEXT NOT NULL DEFAULT '',
            platform        TEXT NOT NULL DEFAULT 'unknown',
            started_at_ms   INTEGER NOT NULL,
            ended_at_ms     INTEGER,
            title           TEXT,
            source          TEXT NOT NULL DEFAULT 'manual',
            kind            TEXT NOT NULL DEFAULT 'live' CHECK (kind IN ('live', 'rehearsal')),
            heat            INTEGER NOT NULL DEFAULT 0,
            viewer_count    INTEGER NOT NULL DEFAULT 0,
            audience_total  INTEGER NOT NULL DEFAULT 0,
            updated_at_ms   INTEGER NOT NULL,
            notebook_text   TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE session_recaps (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL UNIQUE,
            started_at_ms  INTEGER NOT NULL,
            ended_at_ms    INTEGER NOT NULL,
            recap_text     TEXT NOT NULL,
            created_at_ms  INTEGER NOT NULL
        );
        INSERT INTO live_sessions (id, started_at_ms, ended_at_ms, updated_at_ms)
            VALUES (1, 1000, 2000, 2000), (2, 3000, 4000, 4000), (3, 5000, NULL, 5000);
        """
    )


def test_v24_callback_backfills_settled_and_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_v23_schema(conn)

    v24_migrate(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(live_sessions)").fetchall()}
    assert {"settled_at_ms", "settle_attempts", "settle_report"} <= columns
    rows = {int(row["id"]): row for row in conn.execute("SELECT * FROM live_sessions").fetchall()}
    # 存量已结束场次一律视为已整理，不集中补跑历史场次
    assert rows[1]["settled_at_ms"] == 2000
    assert rows[2]["settled_at_ms"] == 4000
    assert rows[3]["settled_at_ms"] is None
    assert rows[1]["settle_attempts"] == 0
    assert rows[1]["settle_report"] is None

    # 幂等：再跑一次不会把新写的待整理场次误标成已整理
    conn.execute("UPDATE live_sessions SET settled_at_ms=NULL WHERE id=2")
    v24_migrate(conn)
    row = conn.execute("SELECT settled_at_ms FROM live_sessions WHERE id=2").fetchone()
    assert row["settled_at_ms"] is None
    conn.close()


def test_v24_leaves_latest_real_live_session_for_settling() -> None:
    """旧记忆在同一次升级里退役、新账本是空的：最近一场够长的正式场留着待整理，启动补整理从它长出
    第一份档案与承诺。更早的正式场、彩排场、开错后马上重开的一两分钟照旧标成已整理。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_v23_schema(conn)
    minute = 60_000
    conn.executescript(
        f"""
        INSERT INTO live_sessions (id, started_at_ms, ended_at_ms, kind, updated_at_ms) VALUES
            (10, 100000000, {100000000 + 38 * minute}, 'live', 0),
            (11, 200000000, {200000000 + 49 * minute}, 'live', 0),
            (12, 300000000, {300000000 + 30 * minute}, 'rehearsal', 0),
            (13, 400000000, {400000000 + 1 * minute}, 'live', 0);
        """
    )

    v24_migrate(conn)

    settled = {
        int(row["id"]): row["settled_at_ms"] for row in conn.execute("SELECT id, settled_at_ms FROM live_sessions")
    }
    assert settled[11] is None, "最近一场够长的正式场留给启动补整理"
    assert all(settled[pk] is not None for pk in (1, 2, 10, 12, 13))
    assert settled[3] is None, "没结束的场次本来就待整理"

    # 幂等：列已补齐，再跑一次不再改任何一行
    conn.execute("UPDATE live_sessions SET settled_at_ms=1 WHERE id=11")
    v24_migrate(conn)
    assert conn.execute("SELECT settled_at_ms FROM live_sessions WHERE id=11").fetchone()[0] == 1
    conn.close()


def test_v24_callback_tolerates_missing_live_sessions_table() -> None:
    conn = sqlite3.connect(":memory:")
    v24_migrate(conn)
    conn.close()


def test_schema_expected_tables_include_memory_ledger() -> None:
    assert SCHEMA_VERSION >= 24  # 账本自 v24 起在位；后续版本只做旧存储清理
    tables = list_expected_tables()
    assert "commitments" in tables
    assert "archive_lines" in tables


async def test_fresh_database_creates_ledger_tables(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "fresh.db")
    await db.initialize()
    try:
        rows = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {str(row["name"]) for row in rows}
        assert {"commitments", "archive_lines"} <= names
        columns = await db.execute("PRAGMA table_info(live_sessions)")
        column_names = {str(row["name"]) for row in columns}
        assert {"settled_at_ms", "settle_attempts", "settle_report"} <= column_names
    finally:
        await db.close()
