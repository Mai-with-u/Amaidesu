"""迁移前自动备份：旧库/版本落后的库升级前备份，全新库不备份，备份失败不阻塞，备份不自动清理"""

from __future__ import annotations

import sqlite3
from datetime import datetime, tzinfo
from pathlib import Path

import pytest

from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.schema import SCHEMA_VERSION


def _backup_files(db_path: Path) -> list[Path]:
    backup_dir = db_path.parent / "backups"
    return sorted(backup_dir.glob("*.db")) if backup_dir.is_dir() else []


def _make_legacy_db(db_path: Path) -> None:
    """ "前版本时代"的旧库：有业务表与数据，但无 schema_migrations。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE legacy_tbl (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO legacy_tbl (note) VALUES ('升级前的数据')")
    conn.commit()
    conn.close()


async def _initialize(db_path: Path) -> list[str]:
    """初始化到当前版本，返回 legacy_tbl 里保留的数据（无该表则为空）。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        assert await db.get_schema_version() == SCHEMA_VERSION
        if not await db.table_exists("legacy_tbl"):
            return []
        return [r["note"] for r in await db.execute("SELECT note FROM legacy_tbl")]
    finally:
        await db.close()


async def test_legacy_db_backed_up_then_upgraded(db_path: Path) -> None:
    _make_legacy_db(db_path)
    assert await _initialize(db_path) == ["升级前的数据"]

    (backup,) = _backup_files(db_path)
    conn = sqlite3.connect(str(backup))
    assert conn.execute("SELECT note FROM legacy_tbl").fetchall() == [("升级前的数据",)]
    conn.close()


async def test_version_lagged_db_backed_up_but_fresh_db_not(db_path: Path) -> None:
    await _initialize(db_path)
    assert _backup_files(db_path) == []

    db = SQLiteDatabase(db_path)
    await db.initialize()
    await db.execute("DELETE FROM schema_migrations WHERE version > 1")
    await db.close()
    await _initialize(db_path)
    assert len(_backup_files(db_path)) == 1


async def test_backup_failure_does_not_block_migration(db_path: Path) -> None:
    _make_legacy_db(db_path)
    (db_path.parent / "backups").write_text("not a dir", encoding="utf-8")  # 备份目录被文件占用
    assert await _initialize(db_path) == ["升级前的数据"]


async def test_backups_are_not_auto_pruned(store: SQLiteDatabase, db_path: Path) -> None:
    store._backup_before_migration(SCHEMA_VERSION - 1)
    store._backup_before_migration(SCHEMA_VERSION - 1)
    # 撞名会自动避让，两次备份必然是两个文件，不依赖时钟分辨率
    assert len(_backup_files(db_path)) == 2


async def test_backups_with_identical_timestamp_do_not_overwrite(
    store: SQLiteDatabase, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """时钟返回同一时刻（粗粒度时钟或极快连续迁移）时，备份追加序号避让而非互相覆盖。"""
    frozen = datetime(2026, 10, 15, 12, 0, 0, 123456)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return frozen

    monkeypatch.setattr("src.modules.storage.database.datetime", _FrozenDatetime)

    # 连续三次备份拿到同一时间戳：第一份占原名，后两份依次追加 -1、-2
    for _ in range(3):
        store._backup_before_migration(SCHEMA_VERSION - 1)

    base = f"test-pre-v{SCHEMA_VERSION}-20261015-120000123456"
    assert [path.name for path in _backup_files(db_path)] == sorted([f"{base}.db", f"{base}-1.db", f"{base}-2.db"])
