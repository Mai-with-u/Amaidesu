"""v38 → v39 迁移：承诺最近提起场次与了结来源回填、场内更正表与整理断点表创建、幂等验证。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v39_memory_zones_lifecycle import migrate as v39_migrate
from src.modules.storage.schema import SCHEMA_VERSION, build_schema_sql


def _to_v38(path: Path) -> None:
    """把新库回退到 v38 形态：删掉两张新表，重建不含新两列的 commitments 并塞入三类历史行。"""
    raw = sqlite3.connect(path)
    try:
        raw.execute("DROP TABLE IF EXISTS memory_corrections")
        raw.execute("DROP TABLE IF EXISTS settle_checkpoints")
        raw.execute("DROP TRIGGER IF EXISTS trg_memory_index_commitment_deleted")
        raw.execute("DROP TRIGGER IF EXISTS trg_memory_index_commitment_edited")
        raw.execute("DROP TABLE IF EXISTS commitments")
        raw.execute(
            """
            CREATE TABLE commitments (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                what                  TEXT NOT NULL,
                quote                 TEXT NOT NULL DEFAULT '',
                evidence_chat_id      INTEGER UNIQUE,
                viewer_id             INTEGER,
                game                  TEXT,
                save_id               TEXT NOT NULL DEFAULT '',
                save_name             TEXT NOT NULL DEFAULT '',
                status                TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'void')),
                created_session_id    INTEGER,
                close_chat_id         INTEGER,
                close_quote           TEXT,
                created_at_ms         INTEGER NOT NULL,
                updated_at_ms         INTEGER NOT NULL,
                keywords              TEXT NOT NULL DEFAULT '[]',
                target_player_id      INTEGER REFERENCES game_players(id) ON DELETE CASCADE,
                evidence_game_chat_id INTEGER REFERENCES game_chat(id) ON DELETE SET NULL
            )
            """
        )
        raw.execute(
            "INSERT INTO commitments(id, what, quote, status, created_session_id, close_chat_id, created_at_ms, updated_at_ms)"
            " VALUES"
            " (1, '搭树屋', '下次搭树屋', 'open', 10, NULL, 1000, 1000),"
            " (2, '修木桥', '修好了', 'closed', 8, 99, 900, 1200),"
            " (3, '误记的事', '', 'void', 7, NULL, 800, 1100)"
        )
        raw.execute("DELETE FROM schema_migrations WHERE version > 38")
        raw.commit()
    finally:
        raw.close()


def test_v39_backfills_columns_creates_tables_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "v39.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(build_schema_sql())
        conn.close()
        _to_v38(db_path)
        conn = sqlite3.connect(db_path)
        v39_migrate(conn)
        v39_migrate(conn)

        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "memory_corrections" in tables
        assert "settle_checkpoints" in tables

        rows = conn.execute(
            "SELECT id, last_mentioned_session_id, close_source FROM commitments ORDER BY id"
        ).fetchall()
        assert rows == [
            (1, 10, ""),
            (2, 8, "settle"),
            (3, 7, "human"),
        ]
    finally:
        conn.close()


async def test_v38_db_upgrades_to_current_schema(tmp_path: Path) -> None:
    path = tmp_path / "upgrade" / "amaidesu.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    _to_v38(path)

    upgraded = SQLiteDatabase(path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        assert await upgraded.table_exists("memory_corrections")
        assert await upgraded.table_exists("settle_checkpoints")
        c1 = await upgraded.commitments.get_commitment(commitment_id=1)
        c2 = await upgraded.commitments.get_commitment(commitment_id=2)
        c3 = await upgraded.commitments.get_commitment(commitment_id=3)
        assert c1 is not None and c1.last_mentioned_session_id == 10 and c1.close_source == ""
        assert c2 is not None and c2.last_mentioned_session_id == 8 and c2.close_source == "settle"
        assert c3 is not None and c3.last_mentioned_session_id == 7 and c3.close_source == "human"
    finally:
        await upgraded.close()
