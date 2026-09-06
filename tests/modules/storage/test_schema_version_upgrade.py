"""
Schema 版本升级机制单测

覆盖：
- 新库直接建到当前 SCHEMA_VERSION，schema_migrations 补齐 [1, N] 全部版本记录
- 旧库（版本记录停留在 1）重新 initialize 后单调推进到当前版本，数据不被破坏
- 模块私有表（_memory_facts / _memory_profiles）随 store.initialize() 统一建立，
  且不在启动闸门 list_expected_tables() 里
- SimpleMemory.initialize() 在私有表缺失时 fail-fast
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.memory.simple_memory import SimpleMemory
from src.modules.storage.schema import (
    SCHEMA_VERSION,
    list_expected_tables,
    list_private_tables,
)
from src.modules.storage.sqlite_store import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="storage-schema-upgrade-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_fresh_db_records_all_versions_up_to_current(store: SQLiteStore) -> None:
    version = await store.get_schema_version()
    assert version == SCHEMA_VERSION
    rows = await store.execute("SELECT version FROM schema_migrations ORDER BY version")
    assert [r["version"] for r in rows] == list(range(1, SCHEMA_VERSION + 1))


@pytest.mark.asyncio
async def test_old_db_upgrades_monotonically(temp_db_path: Path) -> None:
    # 先造一个"版本停留在 1"的旧库
    old = SQLiteStore(temp_db_path)
    await old.initialize()
    await old.execute("DELETE FROM schema_migrations WHERE version > 1")
    assert await old.get_schema_version() == 1
    # 旧库预置一行业务数据，升级后必须原样保留
    await old.insert_live_chat(
        live_session_id=7,
        timestamp_ms=1_000,
        sender_role="viewer",
        content="升级前的话",
        message_type="danmaku",
    )
    await old.close()

    reopened = SQLiteStore(temp_db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        rows = await reopened.execute("SELECT content FROM live_chat")
        assert [r["content"] for r in rows] == ["升级前的话"]
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_private_tables_created_by_store_initialize(store: SQLiteStore) -> None:
    for table in list_private_tables():
        assert await store.table_exists(table), f"私有表 {table} 未随 store.initialize() 建立"
    # 索引也随表建立
    idx = await store.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_memory_facts%'")
    assert len(idx) >= 2
    # 私有表不进入"缺一即拒启"闸门
    assert not set(list_private_tables()) & set(list_expected_tables())


@pytest.mark.asyncio
async def test_simple_memory_initialize_fails_fast_on_missing_tables(temp_db_path: Path) -> None:
    store = SQLiteStore(temp_db_path, auto_apply_schema=False)
    await store.initialize()
    try:
        memory = SimpleMemory(store)
        with pytest.raises(RuntimeError, match="_memory_facts"):
            await memory.initialize()
    finally:
        await store.close()
