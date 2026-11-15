"""v43 → v44 迁移：场次行补 notes 与 overview 两列、幂等升级与仓储读写验证。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v44_session_notes_and_overview import migrate as v44_migrate
from src.modules.storage.schema import SCHEMA_VERSION, build_schema_sql


def _to_v43(path: Path) -> None:
    """把新库回退到 v43 形态：live_sessions 去掉 notes 与 overview 两列，塞入一行历史场次。"""
    raw = sqlite3.connect(path)
    try:
        raw.execute("ALTER TABLE live_sessions DROP COLUMN notes")
        raw.execute("ALTER TABLE live_sessions DROP COLUMN overview")
        raw.execute(
            "INSERT INTO live_sessions(id, stream_id, platform, started_at_ms, ended_at_ms, kind, updated_at_ms)"
            " VALUES (1, 'room', 'bilibili', 1000, 2000000, 'live', 2000000)"
        )
        raw.execute("DELETE FROM schema_migrations WHERE version > 43")
        raw.commit()
    finally:
        raw.close()


def test_v44_adds_both_columns_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "v44.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(build_schema_sql())
        conn.close()
        _to_v43(db_path)
        conn = sqlite3.connect(db_path)
        v44_migrate(conn)
        v44_migrate(conn)

        row = conn.execute("SELECT id, title, notes, overview FROM live_sessions WHERE id=1").fetchone()
        assert row == (1, None, None, None)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(live_sessions)")}
        assert {"notes", "overview"} <= columns
    finally:
        conn.close()


async def test_v43_db_upgrades_and_persists_notes_and_overview(tmp_path: Path) -> None:
    path = tmp_path / "upgrade" / "amaidesu.db"
    fresh = SQLiteDatabase(path)
    await fresh.initialize()
    await fresh.close()
    _to_v43(path)

    store = SQLiteDatabase(path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION
        legacy = await store.sessions.get_live_session(live_session_id=1)
        assert legacy is not None and legacy.notes is None and legacy.overview is None

        # 备注写入后读回；清掉后回到未写状态
        assert (
            await store.sessions.update_live_session_notes(
                live_session_id=1, notes="这场水车翻车了", updated_at_ms=3_000
            )
            is True
        )
        row = await store.sessions.get_live_session(live_session_id=1)
        assert row is not None and row.notes == "这场水车翻车了"
        assert (
            await store.sessions.update_live_session_notes(live_session_id=1, notes=None, updated_at_ms=4_000) is True
        )
        row = await store.sessions.get_live_session(live_session_id=1)
        assert row is not None and row.notes is None

        # 概要列：模型写的这场概览，与人写的备注各写各的
        assert (
            await store.sessions.update_live_session_overview(
                live_session_id=1, overview="这场在造水车", updated_at_ms=5_000
            )
            is True
        )
        row = await store.sessions.get_live_session(live_session_id=1)
        assert row is not None and row.overview == "这场在造水车" and row.notes is None
    finally:
        await store.close()
