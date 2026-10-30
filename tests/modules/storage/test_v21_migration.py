"""v20 → v21 迁移：``live_sessions`` 加备忘录全文列，存量场次补空串。

先建最新库，再去掉新列退回 v20 形状、写入存量场次，重新打开触发迁移；
再开一次版本与数据都不变（幂等）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v21_live_session_notebook import migrate as v21_migrate
from src.modules.storage.schema import SCHEMA_VERSION


def _make_v20(path: Path) -> None:
    """去掉备忘录列退回 v20 形状，留两场存量场次（一场已结账、一场进行中）。"""
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE live_sessions DROP COLUMN notebook_text")
    conn.execute("DELETE FROM schema_migrations WHERE version > 20")
    conn.execute(
        "INSERT INTO live_sessions(id, started_at_ms, ended_at_ms, updated_at_ms) VALUES (1, 1000, 2000, 2000)"
    )
    conn.execute("INSERT INTO live_sessions(id, started_at_ms, updated_at_ms) VALUES (2, 3000, 3000)")
    conn.commit()
    conn.close()


async def test_v20_database_gains_empty_notebook_column(db_path: Path) -> None:
    """升级后存量场次的备忘录为空串，进行中的场次照常能写；再开一次零变更。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    await db.close()
    _make_v20(db_path)

    upgraded = SQLiteDatabase(db_path)
    await upgraded.initialize()
    try:
        assert await upgraded.get_schema_version() == SCHEMA_VERSION
        assert (await upgraded.sessions.get_live_session(live_session_id=1)).notebook_text == ""
        assert await upgraded.sessions.set_session_notebook(live_session_id=2, notebook_text="本场结论") is True
    finally:
        await upgraded.close()

    reopened = SQLiteDatabase(db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        assert (await reopened.sessions.get_live_session(live_session_id=2)).notebook_text == "本场结论"
    finally:
        await reopened.close()


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
