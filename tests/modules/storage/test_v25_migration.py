"""v24 → v25 迁移：旧记忆存储整体退役。

覆盖：
- 全新库：``session_recaps`` / ``viewer_profiles`` 两张表与 ``live_sessions.notebook_text` 列都不存在，
  启动闸门清单里也没有；
- v24 旧库（带两张表、备忘录列与库旁 ``memory/`` 目录）升级后表 / 列都被清掉，场次行
  本身保留；旧记忆目录整目录移进 ``backups/memory-pre-v25-*/``，文件内容原样保留；
- 备份目录不可用时目录原地保留（只告警，不丢数据、不阻塞升级）；
- 重复执行幂等：升级后再开一次不报错，不会重复搬第二份；回调对旧形状重复调用安全。
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v25_drop_legacy_memory import migrate as v25_migrate
from src.modules.storage.schema import SCHEMA_VERSION, list_expected_tables

_LEGACY_TABLES = ("session_recaps", "viewer_profiles")


async def _open(path: Path) -> SQLiteDatabase:
    db = SQLiteDatabase(path)
    await db.initialize()
    return db


def _memory_backups(db_path: Path) -> list[Path]:
    """库旁备份目录里的旧记忆备份目录（按名字排序，便于断言只搬了一份）。"""
    backup_dir = db_path.parent / "backups"
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob("memory-pre-v25-*"))


def _column_names(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _seed_v24_shape(db_path: Path) -> Path:
    """把已建好的最新库退回 v24 形状：补回两张旧表与备忘录列，并造库旁旧记忆目录。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE session_recaps (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                live_session_id        INTEGER NOT NULL UNIQUE,
                started_at_ms          INTEGER NOT NULL,
                ended_at_ms            INTEGER NOT NULL,
                recap_text             TEXT NOT NULL,
                created_at_ms          INTEGER NOT NULL,
                long_memory_merged_at_ms INTEGER,
                settled_at_ms          INTEGER
            );
            CREATE TABLE viewer_profiles (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                viewer_id             INTEGER NOT NULL UNIQUE,
                profile_text          TEXT NOT NULL,
                last_compressed_at_ms INTEGER NOT NULL DEFAULT 0,
                updated_at_ms         INTEGER NOT NULL
            );
            """
        )
        conn.execute("ALTER TABLE live_sessions ADD COLUMN notebook_text TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "INSERT INTO live_sessions(id, started_at_ms, ended_at_ms, updated_at_ms, notebook_text)"
            " VALUES (1, 1000, 2000, 2000, '旧备忘录')"
        )
        conn.execute(
            "INSERT INTO session_recaps(live_session_id, started_at_ms, ended_at_ms, recap_text, created_at_ms)"
            " VALUES (1, 1000, 2000, '旧回顾', 1600)"
        )
        conn.execute("INSERT INTO viewer_profiles(viewer_id, profile_text, updated_at_ms) VALUES (1, '旧画像', 10)")
        conn.execute("DELETE FROM schema_migrations WHERE version > 24")
        conn.commit()
    finally:
        conn.close()

    memory_dir = db_path.parent / "memory"
    (memory_dir / "snapshots").mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("## 老梗\n- 小雨上次起哄的梗\n", encoding="utf-8")
    (memory_dir / "snapshots" / "2026-01-01.md").write_text("旧快照", encoding="utf-8")
    return memory_dir


async def test_fresh_database_has_no_legacy_memory_storage(db_path: Path) -> None:
    assert SCHEMA_VERSION >= 25
    for gone in _LEGACY_TABLES:
        assert gone not in list_expected_tables()

    db = await _open(db_path)
    try:
        await db.assert_schema_ready()
        for gone in _LEGACY_TABLES:
            assert await db.table_exists(gone) is False
        assert "notebook_text" not in {r["name"] for r in await db.execute("PRAGMA table_info(live_sessions)")}
    finally:
        await db.close()


async def test_v24_database_upgrade_drops_tables_column_and_memory_dir(db_path: Path) -> None:
    await (await _open(db_path)).close()  # 建当前形状的库，再退回 v24（v25 之前这些表、列由旧库持有）
    memory_dir = _seed_v24_shape(db_path)
    assert memory_dir.is_dir()

    db = await _open(db_path)
    try:
        assert await db.get_schema_version() == SCHEMA_VERSION
        for gone in _LEGACY_TABLES:
            assert await db.table_exists(gone) is False
        assert "notebook_text" not in {r["name"] for r in await db.execute("PRAGMA table_info(live_sessions)")}
        # 只删列，不删场次行本身
        (row,) = await db.execute("SELECT ended_at_ms FROM live_sessions WHERE id=1")
        assert row["ended_at_ms"] == 2_000
    finally:
        await db.close()

    assert memory_dir.exists() is False
    assert _column_names(db_path, "live_sessions").isdisjoint({"notebook_text"})
    # 目录整份移进备份：文件内容原样保留，可手工取回
    backups = _memory_backups(db_path)
    assert len(backups) == 1
    moved = backups[0]
    assert moved.is_dir()
    assert (moved / "MEMORY.md").read_text(encoding="utf-8") == "## 老梗\n- 小雨上次起哄的梗\n"
    assert (moved / "snapshots" / "2026-01-01.md").read_text(encoding="utf-8") == "旧快照"

    # 再开一次：版本已是最新，不重跑迁移也不重建旧结构、不搬第二份
    reopened = await _open(db_path)
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        for gone in _LEGACY_TABLES:
            assert await reopened.table_exists(gone) is False
    finally:
        await reopened.close()
    assert memory_dir.exists() is False
    assert _memory_backups(db_path) == backups


async def test_moved_memory_dir_counts_as_backed_up_now(db_path: Path) -> None:
    """移进备份的旧记忆目录从移入那一刻起算保留期，不沿用目录原来的修改时刻。

    保留期按备份条目自身的修改时刻判定；改名不更新目录的修改时刻，几个月没动过的旧目录
    若不重置，升级后同一次启动的保留期清理就会把刚移进来的唯一一份旧笔记删掉。
    """
    await (await _open(db_path)).close()
    memory_dir = _seed_v24_shape(db_path)
    ninety_days_ago = time.time() - 90 * 24 * 3600
    os.utime(memory_dir, (ninety_days_ago, ninety_days_ago))

    before = time.time()
    await (await _open(db_path)).close()

    (moved,) = _memory_backups(db_path)
    assert moved.stat().st_mtime >= before - 5, "移入备份后目录修改时刻应为移入时刻"


def test_memory_dir_kept_when_backup_target_unusable(tmp_path: Path) -> None:
    """备份目录不可用（同名文件占位）时：目录原地保留，文件名与内容不变，迁移不抛异常。"""
    db_path = tmp_path / "amaidesu.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE live_sessions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " started_at_ms INTEGER NOT NULL,"
            " updated_at_ms INTEGER NOT NULL)"
        )
        memory_dir = tmp_path / "memory"
        (memory_dir / "snapshots").mkdir(parents=True)
        (memory_dir / "MEMORY.md").write_text("旧笔记", encoding="utf-8")
        (tmp_path / "backups").write_text("占位：不是目录", encoding="utf-8")

        v25_migrate(conn)

        assert memory_dir.is_dir()
        assert (memory_dir / "MEMORY.md").read_text(encoding="utf-8") == "旧笔记"
    finally:
        conn.close()


def test_migrate_is_idempotent_on_legacy_shape() -> None:
    """回调本身对旧形状可重复执行：第二次调用时表、列、目录都已不在，直接跳过。"""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(
            """
            CREATE TABLE live_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                notebook_text TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE session_recaps (id INTEGER PRIMARY KEY, live_session_id INTEGER NOT NULL);
            CREATE TABLE viewer_profiles (id INTEGER PRIMARY KEY, viewer_id INTEGER NOT NULL);
            """
        )
        for _ in range(2):
            v25_migrate(conn)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables.isdisjoint(_LEGACY_TABLES)
        assert "notebook_text" not in {row[1] for row in conn.execute("PRAGMA table_info(live_sessions)")}
    finally:
        conn.close()
