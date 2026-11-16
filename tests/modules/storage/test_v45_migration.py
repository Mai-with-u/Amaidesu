"""v44 → v45 迁移：幂等补齐 live_sessions 的 notes 与 overview 两列。

覆盖三种库形态：
- 事故态：版本 44 已记录、notes 列在、overview 列缺（半成品迁移占号后真库停留的中间态）；
- 全新库（当前 DDL 直接两列齐全，迁移为空操作）；
- 已完整库（重复执行幂等跳过，不再产生变更）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v45_session_overview_backfill import migrate as v45_migrate
from src.modules.storage.schema import SCHEMA_VERSION, build_schema_sql


def _columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(live_sessions)")}
    finally:
        conn.close()


def _make_accident_state(db_path: Path) -> None:
    """构造事故态库：当前代码建库后，去掉 overview 列、版本记录回退到 44（notes 保留在库）。"""
    raw = sqlite3.connect(db_path)
    try:
        raw.executescript(build_schema_sql())
        raw.execute("ALTER TABLE live_sessions DROP COLUMN overview")
        raw.execute("DELETE FROM schema_migrations")
        for version in range(1, 45):
            raw.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (?, 0)", (version,))
        raw.execute(
            "INSERT INTO live_sessions(id, stream_id, platform, started_at_ms, ended_at_ms, kind, updated_at_ms)"
            " VALUES (1, 'room', 'bilibili', 1000, 2000000, 'live', 2000000)"
        )
        raw.commit()
    finally:
        raw.close()


def test_accident_state_backfills_both_columns_keeping_data(tmp_path: Path) -> None:
    """版本 44 已记录、notes 在、overview 缺：跑 v45 后两列齐、数据行无损（落盘验证）。"""
    db_path = tmp_path / "accident.db"
    _make_accident_state(db_path)
    assert "notes" in _columns(db_path)
    assert "overview" not in _columns(db_path)

    conn = sqlite3.connect(db_path)
    try:
        v45_migrate(conn)
        conn.commit()
        row = conn.execute("SELECT id, notes, overview FROM live_sessions WHERE id=1").fetchone()
        assert row == (1, None, None)
    finally:
        conn.close()

    # 写回落盘：另开连接确认两列都在、版本记录推进
    assert {"notes", "overview"} <= _columns(db_path)
    reopen = sqlite3.connect(db_path)
    try:
        recorded = [r[0] for r in reopen.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert 44 in recorded
        row = reopen.execute("SELECT id, notes, overview FROM live_sessions WHERE id=1").fetchone()
        assert row == (1, None, None)
    finally:
        reopen.close()


async def test_accident_state_db_upgrades_via_full_chain(tmp_path: Path) -> None:
    """事故态库经 SQLiteDatabase.initialize 走真实升级链：版本到位、两列齐、仓储可写概览。"""
    db_path = tmp_path / "chain.db"
    _make_accident_state(db_path)

    store = SQLiteDatabase(db_path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION
        assert {"notes", "overview"} <= {r["name"] for r in await store.execute("PRAGMA table_info(live_sessions)")}
        assert (
            await store.sessions.update_live_session_overview(
                live_session_id=1, overview="这场在造水车", updated_at_ms=5_000
            )
            is True
        )
        row = await store.sessions.get_live_session(live_session_id=1)
        assert row is not None and row.overview == "这场在造水车"
    finally:
        await store.close()


async def test_fresh_and_complete_dbs_skip_idempotently(tmp_path: Path) -> None:
    """全新库（DDL 已含两列）跑 v45 为空操作；已完整库重复执行幂等，行数不变。"""
    db_path = tmp_path / "fresh.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(build_schema_sql())
        conn.execute(
            "INSERT INTO live_sessions(id, stream_id, platform, started_at_ms, ended_at_ms, kind, updated_at_ms)"
            " VALUES (1, 'room', 'bilibili', 1000, 2000000, 'live', 2000000)"
        )
        conn.commit()
        for _ in range(2):
            v45_migrate(conn)
            conn.commit()
        rows = conn.execute("SELECT id, notes, overview FROM live_sessions").fetchall()
        assert rows == [(1, None, None)]
        assert {"notes", "overview"} <= {r[1] for r in conn.execute("PRAGMA table_info(live_sessions)")}
    finally:
        conn.close()
