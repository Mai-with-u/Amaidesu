"""迁移账本内容指纹：执行记账存指纹、启动校验拦"同版本号不同内容"。

覆盖：全新库记账含指纹；账本指纹与脚本不符硬错且点名版本；账本记录了
注册表中已不存在的版本硬错；v46 回填后老账本（无指纹态）启动校验放行。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Dict

import pytest

from src.modules.storage import database as database_module
from src.modules.storage.database import SchemaMigrationTamperedError, SQLiteDatabase
from src.modules.storage.migrations import SCHEMA_MIGRATION_FILES
from src.modules.storage.migrations._common import compute_content_hash
from src.modules.storage.schema import SCHEMA_VERSION, build_schema_sql


async def _open(path: Path) -> SQLiteDatabase:
    """打开库并完成 schema 应用；调用方负责 close。"""
    store = SQLiteDatabase(path)
    await store.initialize()
    return store


def _make_legacy_ledger(db_path: Path) -> None:
    """构造 v46 前的老账本：建库后版本记录回退到 44、指纹全空（无指纹时代的账本形态）。"""
    raw = sqlite3.connect(db_path)
    try:
        raw.executescript(build_schema_sql())
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


async def test_fresh_db_records_hash_for_every_version(db_path: Path) -> None:
    store = await _open(db_path)
    try:
        rows = await store.execute("SELECT version, content_hash FROM schema_migrations ORDER BY version")
        assert [r["version"] for r in rows] == list(range(1, SCHEMA_VERSION + 1))
        for row in rows:
            assert row["content_hash"] == compute_content_hash(SCHEMA_MIGRATION_FILES[int(row["version"])])
    finally:
        await store.close()


async def test_tampered_ledger_hash_is_rejected_with_version_named(db_path: Path) -> None:
    """账本指纹与脚本文件不符（等价于脚本执行后被改动）：硬错且消息点名版本号。"""
    store = await _open(db_path)
    await store.close()

    tampered_version = SCHEMA_VERSION - 1
    raw = sqlite3.connect(db_path)
    try:
        raw.execute("UPDATE schema_migrations SET content_hash='deadbeef' WHERE version=?", (tampered_version,))
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(SchemaMigrationTamperedError) as exc_info:
        await _open(db_path)
    message = str(exc_info.value)
    assert str(tampered_version) in message
    assert "指纹" in message


async def test_ledger_version_missing_from_registry_is_rejected(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """账本记录的版本在注册表中已无对应迁移：硬错且消息点名版本号。"""
    store = await _open(db_path)
    recorded = await store.execute("SELECT MAX(version) AS v FROM schema_migrations")
    highest = int(recorded[0]["v"])
    await store.close()

    reduced_registry: Dict[int, Callable] = {v: m for v, m in database_module.SCHEMA_MIGRATIONS.items() if v != highest}
    monkeypatch.setattr(database_module, "SCHEMA_MIGRATIONS", reduced_registry)

    with pytest.raises(SchemaMigrationTamperedError) as exc_info:
        await _open(db_path)
    assert str(highest) in str(exc_info.value)


async def test_legacy_ledger_without_hash_passes_after_backfill(tmp_path: Path) -> None:
    """v46 前的老账本（版本 44 已记录、指纹全空）经回填迁移后启动校验通过、指纹齐全。"""
    db_path = tmp_path / "legacy.db"
    _make_legacy_ledger(db_path)

    store = SQLiteDatabase(db_path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION
        rows = await store.execute("SELECT version, content_hash FROM schema_migrations ORDER BY version")
        assert [r["version"] for r in rows] == list(range(1, SCHEMA_VERSION + 1))
        for row in rows:
            assert row["content_hash"] == compute_content_hash(SCHEMA_MIGRATION_FILES[int(row["version"])])
    finally:
        await store.close()
