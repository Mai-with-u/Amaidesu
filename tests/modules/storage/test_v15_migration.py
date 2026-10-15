"""v15 迁移单测：场次回顾表上线 + 观众事实表退役。

覆盖：
- 新库建表即含 session_recaps、不含 viewer_facts（版本随后续迁移继续推进）
- v14 形状旧库升级：viewer_facts 被 DROP、viewer_profiles 原样保留、重复执行幂等
- 场次删除级联清掉该场的回顾行
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v15_session_recaps_drop_viewer_facts import migrate as v15_migrate
from src.modules.storage.schema import SCHEMA_VERSION, list_expected_tables


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="v15-migration-"))
    yield td
    shutil.rmtree(td, ignore_errors=True)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def test_new_database_has_recaps_and_no_facts(temp_dir: Path) -> None:
    """新库：session_recaps 在启动闸门内，viewer_facts 不再建表。"""
    assert SCHEMA_VERSION >= 15
    assert "session_recaps" in list_expected_tables()
    assert "viewer_facts" not in list_expected_tables()
    db = SQLiteDatabase(temp_dir / "fresh.db")
    asyncio.run(db.initialize())
    try:
        conn = sqlite3.connect(temp_dir / "fresh.db")
        assert _table_exists(conn, "session_recaps")
        assert not _table_exists(conn, "viewer_facts")
        conn.close()
    finally:
        asyncio.run(db.close())


def test_v14_database_upgrade_drops_facts_keeps_profiles(temp_dir: Path) -> None:
    """v14 形状旧库：事实表被删，常客印象行原样保留，回调幂等。"""
    db_path = temp_dir / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE viewer_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, user_id TEXT NOT NULL,
            fact_text TEXT NOT NULL, source_message_id TEXT NOT NULL DEFAULT '', created_at_ms INTEGER NOT NULL
        );
        CREATE TABLE viewer_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, user_id TEXT NOT NULL,
            profile_text TEXT NOT NULL, last_compressed_at_ms INTEGER NOT NULL DEFAULT 0,
            updated_at_ms INTEGER NOT NULL, UNIQUE(platform, user_id)
        );
        INSERT INTO viewer_facts(platform, user_id, fact_text, created_at_ms) VALUES ('bilibili', 'u1', '喜欢数码', 1);
        INSERT INTO viewer_profiles(platform, user_id, profile_text, last_compressed_at_ms, updated_at_ms)
            VALUES ('bilibili', 'u1', '老粉，常聊数码', 1, 2);
        """
    )
    conn.commit()

    v15_migrate(conn)
    assert not _table_exists(conn, "viewer_facts")
    row = conn.execute("SELECT profile_text FROM viewer_profiles WHERE user_id='u1'").fetchone()
    assert row[0] == "老粉，常聊数码"

    # 幂等：再跑一次不报错、印象仍在
    v15_migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM viewer_profiles").fetchone()[0] == 1
    conn.close()


@pytest.mark.asyncio
async def test_delete_session_cascades_recap(temp_dir: Path) -> None:
    """删除场次时该场的回顾一并清掉（回顾以场次主键引用）。"""
    db = SQLiteDatabase(temp_dir / "cascade.db")
    await db.initialize()
    try:
        pk = await db.sessions.insert_live_session(stream_id="r", platform="bilibili", started_at_ms=1_000)
        await db.sessions.close_live_session(live_session_id=pk, ended_at_ms=2_000)
        assert await db.recaps.upsert_recap(
            live_session_id=pk, started_at_ms=1_000, ended_at_ms=2_000, recap_text="聊了聊"
        )
        assert await db.recaps.get_recap(live_session_id=pk) is not None

        assert await db.sessions.delete_live_session(live_session_id=pk) is True
        assert await db.recaps.get_recap(live_session_id=pk) is None
    finally:
        await db.close()
