"""v19 → v20 迁移：``session_recaps`` 加收播整理进度两列，存量回顾视为已整理。

先建最新库，再去掉两列退回 v19 形状、写入存量场次与回顾，重新打开触发迁移。
存量回顾行两列都回填为回顾写入时刻，升级后启动补写不会把历史场次重跑一遍；
没有回顾的已结束场次照旧留在待办里（与升级前的补写口径一致）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v20_session_recap_settlement import migrate as v20_migrate
from src.modules.storage.schema import SCHEMA_VERSION

_PROGRESS_COLUMNS = ("long_memory_merged_at_ms", "settled_at_ms")


def _make_v19(path: Path) -> None:
    """去掉整理进度两列退回 v19 形状：场次 1、2 有回顾，场次 3 已结束但没回顾。"""
    conn = sqlite3.connect(path)
    for column in _PROGRESS_COLUMNS:
        conn.execute(f"ALTER TABLE session_recaps DROP COLUMN {column}")
    conn.execute("DELETE FROM schema_migrations WHERE version > 19")
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
    conn.commit()
    conn.close()


def _progress(path: Path) -> dict[int, tuple[int | None, int | None]]:
    conn = sqlite3.connect(path)
    try:
        return {
            int(r[0]): (r[1], r[2])
            for r in conn.execute("SELECT live_session_id, long_memory_merged_at_ms, settled_at_ms FROM session_recaps")
        }
    finally:
        conn.close()


async def test_new_database_recap_starts_unsettled(db_path: Path) -> None:
    """新库：DDL 已含两列，新写的回顾两列为空（整理还没走完）。"""
    assert SCHEMA_VERSION >= 20
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        await db.recaps.upsert_recap(live_session_id=7, started_at_ms=1, ended_at_ms=2, recap_text="新回顾")
        row = await db.recaps.get_recap(live_session_id=7)
        assert row is not None and row.long_memory_merged_at_ms is None and row.settled_at_ms is None
    finally:
        await db.close()


async def test_v19_database_treats_existing_recaps_as_settled(db_path: Path) -> None:
    """v19 旧库升级：存量回顾两列填回顾写入时刻；待办只剩没回顾的场次；再开一次零变更。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    await db.close()
    _make_v19(db_path)

    upgraded = SQLiteDatabase(db_path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        pending = await upgraded.recaps.list_unsettled_sessions(limit=5)
        assert [row.live_session_id for row in pending] == [3]
    finally:
        await upgraded.close()
    expected = {1: (1_600, 1_600), 2: (2_600, 2_600)}
    assert _progress(db_path) == expected

    reopened = SQLiteDatabase(db_path)
    await reopened.initialize()
    await reopened.close()
    assert _progress(db_path) == expected


async def test_rerun_keeps_new_recap_unsettled(db_path: Path) -> None:
    """回填只在补列那一次执行：列已存在时重跑回调，不把新写的、没整理完的回顾误标成已整理。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        await db.recaps.upsert_recap(live_session_id=9, started_at_ms=1, ended_at_ms=2, recap_text="刚写的回顾")
    finally:
        await db.close()

    conn = sqlite3.connect(db_path)
    try:
        v20_migrate(conn)
        conn.commit()
    finally:
        conn.close()
    assert _progress(db_path) == {9: (None, None)}
