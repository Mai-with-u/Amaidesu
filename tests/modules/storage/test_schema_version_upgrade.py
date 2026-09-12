"""
Schema 版本升级机制单测

覆盖：
- 新库直接建到当前 SCHEMA_VERSION，schema_migrations 补齐 [1, N] 全部版本记录
- 旧库（版本记录停留在 1）重新 initialize 后单调推进到当前版本，数据不被破坏
- 模块私有表（_memory_facts）随 store.initialize() 统一建立，
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
from src.modules.storage.migrations import SCHEMA_MIGRATIONS
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


def test_migration_registry_covers_every_version() -> None:
    """守护严格 +1 版本纪律：1..SCHEMA_VERSION 每版都在迁移注册表内。"""
    missing = set(range(1, SCHEMA_VERSION + 1)) - set(SCHEMA_MIGRATIONS)
    extra = set(SCHEMA_MIGRATIONS) - set(range(1, SCHEMA_VERSION + 1))
    assert not missing and not extra, f"迁移注册表与版本链不一致: 缺失版本={sorted(missing)} 多余版本={sorted(extra)}"


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


@pytest.mark.asyncio
async def test_v3_to_v4_migration_semantics(temp_db_path: Path) -> None:
    """v3 形状的旧库升级到 v4：source 列标记 legacy + 悬空行封闭 + 回复关联列 + 索引。"""
    import sqlite3

    conn = sqlite3.connect(temp_db_path)
    conn.executescript(
        """
        CREATE TABLE live_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            started_at_ms INTEGER NOT NULL,
            ended_at_ms INTEGER,
            title TEXT,
            heat INTEGER NOT NULL DEFAULT 0,
            viewer_count INTEGER NOT NULL DEFAULT 0,
            audience_total INTEGER NOT NULL DEFAULT 0,
            updated_at_ms INTEGER NOT NULL
        );
        CREATE TABLE live_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            sender_role TEXT NOT NULL,
            sender_id TEXT,
            sender_name TEXT,
            content TEXT NOT NULL,
            message_type TEXT NOT NULL,
            tool_result TEXT,
            simulated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at_ms INTEGER NOT NULL
        );
        INSERT INTO schema_migrations(version, applied_at_ms) VALUES (3, 0);
        INSERT INTO live_sessions(id, stream_id, platform, started_at_ms, updated_at_ms)
            VALUES (99, 'room1', 'bilibili', 1000, 5000);
        INSERT INTO live_sessions(id, stream_id, platform, started_at_ms, ended_at_ms, updated_at_ms)
            VALUES (98, 'room1', 'bilibili', 500, 4000, 4000);
        INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type, simulated)
            VALUES (99, 1100, 'viewer', '旧弹幕', 'danmaku', 0);
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(temp_db_path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION

        rows = await store.execute("SELECT * FROM live_sessions ORDER BY id")
        by_id = {int(r["id"]): r for r in rows}
        # 存量行标记 legacy（房间号哈希映射时代的遗留数据）
        assert by_id[99]["source"] == "legacy"
        assert by_id[98]["source"] == "legacy"
        # 悬空遗留行封闭到最后活动时刻；已结账行保留原结束时间
        assert by_id[99]["ended_at_ms"] == 5_000
        assert by_id[98]["ended_at_ms"] == 4_000

        cols = await store.execute("PRAGMA table_info(live_chat)")
        names = {r["name"] for r in cols}
        assert {"message_id", "reply_to_message_id"} <= names

        idx = await store.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_live_chat_message_id'"
        )
        assert len(idx) == 1

        # 业务数据原样保留
        chat = await store.execute("SELECT content FROM live_chat")
        assert [r["content"] for r in chat] == ["旧弹幕"]
    finally:
        await store.close()

    # 幂等：再次 initialize 不破坏数据、不重复迁移
    store2 = SQLiteStore(temp_db_path)
    await store2.initialize()
    try:
        assert await store2.get_schema_version() == SCHEMA_VERSION
        chat = await store2.execute("SELECT content FROM live_chat")
        assert [r["content"] for r in chat] == ["旧弹幕"]
    finally:
        await store2.close()


@pytest.mark.asyncio
async def test_v6_to_v7_migration_drops_memory_profiles(temp_db_path: Path) -> None:
    """v6 形状的旧库升级到 v7：人物画像私有表被幂等 DROP，业务数据保留。"""

    # 造 v7 新库后手工回退版本记录并补建旧表，模拟 v6 存量库
    old = SQLiteStore(temp_db_path)
    await old.initialize()
    await old.execute("DELETE FROM schema_migrations WHERE version > 6")
    await old.execute(
        "CREATE TABLE _memory_profiles ("
        "person_id TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', "
        "tags TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', "
        "updated_at_ms INTEGER NOT NULL)"
    )
    await old.execute(
        "INSERT INTO _memory_profiles(person_id, display_name, tags, summary, updated_at_ms) "
        "VALUES ('u_1', 'alice', 'tag', 'sum', 123)"
    )
    assert await old.get_schema_version() == 6
    await old.close()

    # 重新 initialize：迁移 7 执行 DROP，版本推进到当前
    reopened = SQLiteStore(temp_db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        assert await reopened.table_exists("_memory_profiles") is False
        # 存量业务数据不受影响
        rows = await reopened.execute("SELECT COUNT(*) AS n FROM _memory_facts")
        assert int(rows[0]["n"]) == 0
    finally:
        await reopened.close()

    # 幂等：再次 initialize 成功，表保持不存在
    again = SQLiteStore(temp_db_path)
    await again.initialize()
    try:
        assert await again.get_schema_version() == SCHEMA_VERSION
        assert await again.table_exists("_memory_profiles") is False
    finally:
        await again.close()
