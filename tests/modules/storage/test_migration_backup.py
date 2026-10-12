"""迁移前自动备份机制单测

覆盖：
- 前版本时代的旧库（有业务表、无 schema_migrations）升级时生成备份，数据无损
- 版本落后于 SCHEMA_VERSION 的旧库升级时生成备份
- 全新库不生成备份
- 备份失败不阻塞迁移（降级告警）
- 备份文件不做自动清理（同一目录多次备份共存，由用户自行管理）
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.storage.schema import SCHEMA_VERSION
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="migration-backup-"))
    yield td
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_dir: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_dir / "test.db")
    await s.initialize()
    yield s
    await s.close()


def _backup_files(db_path: Path) -> list[Path]:
    backup_dir = db_path.parent / "backups"
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("*.db"))


def _make_legacy_db(db_path: Path) -> None:
    """造一个"前版本时代"的旧库：有业务表与数据，但无 schema_migrations。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE legacy_tbl (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO legacy_tbl (note) VALUES ('升级前的数据')")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_legacy_db_backed_up_and_upgraded(temp_dir: Path) -> None:
    """旧库升级：先备份（含遗留数据），再推进到当前版本。"""
    db_path = temp_dir / "legacy.db"
    _make_legacy_db(db_path)

    store = SQLiteDatabase(db_path)
    try:
        await store.initialize()
        assert await store.get_schema_version() == SCHEMA_VERSION

        backups = _backup_files(db_path)
        assert len(backups) == 1

        # 备份内容可读且包含遗留数据
        conn = sqlite3.connect(str(backups[0]))
        try:
            rows = conn.execute("SELECT note FROM legacy_tbl").fetchall()
            assert rows == [("升级前的数据",)]
        finally:
            conn.close()

        # 遗留数据在升级后的库中仍然可读
        remain = await store.execute("SELECT note FROM legacy_tbl")
        assert [r["note"] for r in remain] == ["升级前的数据"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_version_lagged_db_backed_up(temp_dir: Path) -> None:
    """版本记录落后（如停在 1）的库升级时也生成备份。"""
    db_path = temp_dir / "lagged.db"
    first = SQLiteDatabase(db_path)
    await first.initialize()
    await first.execute("DELETE FROM schema_migrations WHERE version > 1")
    await first.close()

    reopened = SQLiteDatabase(db_path)
    try:
        await reopened.initialize()
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        assert len(_backup_files(db_path)) == 1
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_fresh_db_no_backup(temp_dir: Path) -> None:
    """全新库（文件不存在）首次初始化不生成备份。"""
    db_path = temp_dir / "fresh.db"
    store = SQLiteDatabase(db_path)
    try:
        await store.initialize()
        assert await store.get_schema_version() == SCHEMA_VERSION
        assert _backup_files(db_path) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_backup_failure_does_not_block_migration(temp_dir: Path) -> None:
    """备份目标不可创建（backups 被文件占用）时降级告警，迁移照常完成。"""
    db_path = temp_dir / "blocked.db"
    _make_legacy_db(db_path)
    (temp_dir / "backups").write_text("not a dir", encoding="utf-8")

    store = SQLiteDatabase(db_path)
    try:
        await store.initialize()
        assert await store.get_schema_version() == SCHEMA_VERSION
        remain = await store.execute("SELECT note FROM legacy_tbl")
        assert [r["note"] for r in remain] == ["升级前的数据"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_backups_not_auto_pruned(store: SQLiteDatabase, temp_dir: Path) -> None:
    """备份不做自动清理：同一库多次备份文件共存，由用户自行管理。"""
    db_path = temp_dir / "test.db"
    store._backup_before_migration(SCHEMA_VERSION - 1)
    store._backup_before_migration(SCHEMA_VERSION - 1)

    backups = _backup_files(db_path)
    assert len(backups) >= 2
