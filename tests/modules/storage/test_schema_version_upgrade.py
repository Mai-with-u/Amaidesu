"""
Schema 版本升级机制单测

覆盖：
- 新库直接建到当前 SCHEMA_VERSION，schema_migrations 补齐 [1, N] 全部版本记录
- 旧库（版本记录停留在 1）重新 initialize 后单调推进到当前版本，数据不被破坏
- 私有表机制已废除：无 ``_`` 前缀表，所有表统一进入启动闸门
- SimpleMemory.initialize() 在画像/事实表缺失时 fail-fast
- v9 → v10 迁移语义：付费明细补全、金额单位统一金瓜子、平台身份复合键、
  ``_memory_facts`` 废弃、画像两表新建
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.memory.simple_memory import SimpleMemory
from src.modules.storage.migrations import SCHEMA_MIGRATIONS
from src.modules.storage.schema import (
    SCHEMA_VERSION,
    build_schema_sql,
    list_expected_tables,
)
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="storage-schema-upgrade-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteDatabase, None]:
    s = SQLiteDatabase(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_fresh_db_records_all_versions_up_to_current(store: SQLiteDatabase) -> None:
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
    old = SQLiteDatabase(temp_db_path)
    await old.initialize()
    await old.execute("DELETE FROM schema_migrations WHERE version > 1")
    assert await old.get_schema_version() == 1
    # 旧库预置一行业务数据，升级后必须原样保留
    await old.chat.insert_live_chat(
        live_session_id=7,
        timestamp_ms=1_000,
        sender_role="viewer",
        content="升级前的话",
        message_type="danmaku",
    )
    await old.close()

    reopened = SQLiteDatabase(temp_db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        rows = await reopened.execute("SELECT content FROM live_chat")
        assert [r["content"] for r in rows] == ["升级前的话"]
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_no_private_tables_all_tables_in_startup_gate(store: SQLiteDatabase) -> None:
    """私有表机制已废除：库内无 ``_`` 前缀表，所有表统一进入缺一即拒启闸门。"""
    rows = await store.execute("SELECT name FROM sqlite_master WHERE type='table'")
    underscored = [r["name"] for r in rows if str(r["name"]).startswith("_")]
    assert underscored == [], f"存在私有前缀表: {underscored}"
    # 启动闸门覆盖全部业务表（自检通过 = list_expected_tables 全部就位）
    await store.assert_schema_ready()


@pytest.mark.asyncio
async def test_simple_memory_initialize_fails_fast_on_missing_tables(temp_db_path: Path) -> None:
    store = SQLiteDatabase(temp_db_path, auto_apply_schema=False)
    await store.initialize()
    try:
        memory = SimpleMemory(store)
        with pytest.raises(RuntimeError, match="viewer_facts"):
            await memory.initialize()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_v6_to_v7_migration_drops_memory_profiles(temp_db_path: Path) -> None:
    """v6 形状的旧库升级到 v7：人物画像私有表被幂等 DROP，业务数据保留。"""

    # 造 v7 新库后手工回退版本记录并补建旧表，模拟 v6 存量库
    old = SQLiteDatabase(temp_db_path)
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
    reopened = SQLiteDatabase(temp_db_path)
    await reopened.initialize()
    try:
        assert await reopened.get_schema_version() == SCHEMA_VERSION
        assert await reopened.table_exists("_memory_profiles") is False
    finally:
        await reopened.close()

    # 幂等：再次 initialize 成功，表保持不存在
    again = SQLiteDatabase(temp_db_path)
    await again.initialize()
    try:
        assert await again.get_schema_version() == SCHEMA_VERSION
        assert await again.table_exists("_memory_profiles") is False
    finally:
        await again.close()


def _make_v9_schema(conn: sqlite3.Connection) -> None:
    """构造 v9 形状的存量库（付费明细无金额列、单键 viewers、_memory_facts 在位）。"""
    conn.executescript(
        """
        CREATE TABLE live_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_id TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT 'unknown',
            started_at_ms INTEGER NOT NULL,
            ended_at_ms INTEGER,
            title TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
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
            message_id TEXT,
            reply_to_message_id TEXT,
            tool_result TEXT,
            simulated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            gift_name TEXT NOT NULL,
            gift_count INTEGER NOT NULL,
            simulated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE super_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            amount REAL NOT NULL,
            message TEXT NOT NULL,
            simulated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            source TEXT NOT NULL,
            score REAL NOT NULL,
            trend REAL NOT NULL,
            duration_ms INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE viewers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL UNIQUE,
            user_name TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            gift_count INTEGER NOT NULL DEFAULT 0,
            replied_count INTEGER NOT NULL DEFAULT 0,
            interaction_count INTEGER NOT NULL DEFAULT 0,
            last_active_ms INTEGER NOT NULL
        );
        CREATE TABLE sim_personas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL UNIQUE,
            user_nickname TEXT NOT NULL,
            role TEXT NOT NULL,
            personality TEXT NOT NULL,
            speaking_style TEXT NOT NULL,
            fans_medal_level INTEGER NOT NULL DEFAULT 0,
            guard_level INTEGER NOT NULL DEFAULT 0,
            context_window_size INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            messages_generated INTEGER NOT NULL DEFAULT 0,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        );
        CREATE TABLE sim_gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gift_id TEXT NOT NULL UNIQUE,
            gift_name TEXT NOT NULL,
            category TEXT NOT NULL,
            weight INTEGER NOT NULL DEFAULT 1,
            data_type TEXT NOT NULL,
            sc_amount_rmb INTEGER,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        );
        CREATE TABLE rundowns (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            segments_json TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        );
        CREATE TABLE game_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL,
            game TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            scene TEXT,
            timestamp_ms INTEGER NOT NULL
        );
        CREATE TABLE timeline_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER NOT NULL,
            start_ms INTEGER NOT NULL,
            end_ms INTEGER NOT NULL,
            summary TEXT NOT NULL,
            tags TEXT
        );
        CREATE TABLE llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            live_session_id INTEGER,
            model_name TEXT NOT NULL,
            assign_name TEXT,
            profile_name TEXT,
            provider_name TEXT NOT NULL,
            request_type TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            cache_hit_tokens INTEGER NOT NULL,
            cache_miss_tokens INTEGER NOT NULL,
            cost REAL NOT NULL,
            duration_ms INTEGER NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            request_id TEXT
        );
        CREATE TABLE llm_requests (
            request_id TEXT PRIMARY KEY,
            timestamp_ms INTEGER NOT NULL,
            client_type TEXT NOT NULL DEFAULT '',
            model_name TEXT NOT NULL DEFAULT '',
            request_params TEXT,
            response_content TEXT,
            reasoning_content TEXT,
            tool_calls TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
            cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
            cost REAL NOT NULL DEFAULT 0,
            success INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            latency_ms INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE _memory_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            importance INTEGER NOT NULL DEFAULT 0,
            timestamp_ms INTEGER NOT NULL
        );
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at_ms INTEGER NOT NULL
        );
        INSERT INTO schema_migrations(version, applied_at_ms) VALUES (1, 0);
        """
    )
    # v2..v9 之间无数据变换的其他版本回调也需跑：直接借注册表从 1 推进到 9 更真实——
    # 这里只造"v9 形状"的表结构，版本记录手动置 9，v10 回调作用于该形状。
    conn.execute("DELETE FROM schema_migrations")
    conn.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (9, 0)")
    # 存量业务数据
    conn.execute(
        "INSERT INTO gifts(live_session_id, timestamp_ms, user_id, user_name, gift_name, gift_count, simulated)"
        " VALUES (1, 100, 'u_old', '老观众', '辣条', 3, 0)"
    )
    conn.execute(
        "INSERT INTO super_chats(live_session_id, timestamp_ms, user_id, user_name, amount, message, simulated)"
        " VALUES (1, 200, 'u_old', '老观众', 50.0, '加油', 0)"
    )
    conn.execute(
        "INSERT INTO viewers(user_id, user_name, message_count, interaction_count, last_active_ms)"
        " VALUES ('u_old', '老观众', 5, 7, 300)"
    )
    conn.execute(
        "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type, simulated)"
        " VALUES (1, 150, 'viewer', '存量弹幕', 'danmaku', 0)"
    )
    conn.execute("INSERT INTO _memory_facts(text, timestamp_ms) VALUES ('旧事实', 100)")


@pytest.mark.asyncio
async def test_v9_to_v10_migration_semantics(temp_db_path: Path) -> None:
    """v9 存量库 → v10：三表补全、金额统一金瓜子、身份复合键、扁平事实废弃、画像两表就位。"""
    conn = sqlite3.connect(temp_db_path)
    _make_v9_schema(conn)
    conn.commit()
    conn.close()

    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION

        # gifts：改名 + 补列 + 存量行 platform 回填
        gift = (await store.execute("SELECT * FROM gifts"))[0]
        assert gift["quantity"] == 3
        assert gift["platform"] == "bilibili"
        assert gift["currency"] == ""

        # super_chats：金额元 → 金瓜子（50.0 元 → 50000），币种标注
        sc = (await store.execute("SELECT * FROM super_chats"))[0]
        assert sc["total_price"] == 50_000
        assert sc["currency"] == "bilibili_gold_coin"
        assert sc["platform"] == "bilibili"

        # viewers：复合键唯一约束 + 付费统计列 + 存量行归入历史生产平台
        viewer = (await store.execute("SELECT * FROM viewers"))[0]
        assert viewer["platform"] == "bilibili"
        assert viewer["paid_count"] == 0
        assert viewer["paid_amount"] == 0
        idx_rows = await store.execute("PRAGMA index_list(viewers)")
        unique_cols: set[tuple[str, ...]] = set()
        for idx in idx_rows:
            if not idx["unique"]:
                continue
            cols = await store.execute(f"PRAGMA index_info({idx['name']})")
            unique_cols.add(tuple(c["name"] for c in cols))
        assert ("platform", "user_id") in unique_cols

        # live_chat：platform 列 + 回填
        chat = (await store.execute("SELECT * FROM live_chat"))[0]
        assert chat["platform"] == "bilibili"

        # _memory_facts 废弃、画像两表新建
        assert await store.table_exists("_memory_facts") is False
        assert await store.table_exists("viewer_facts") is True
        assert await store.table_exists("viewer_profiles") is True
        assert await store.table_exists("guards") is True

        # 新库形态幂等：重复 initialize 不破坏数据
        count = await store.execute("SELECT COUNT(*) AS n FROM viewers")
        assert int(count[0]["n"]) == 1
    finally:
        await store.close()

    again = SQLiteDatabase(temp_db_path)
    await again.initialize()
    try:
        assert await again.get_schema_version() == SCHEMA_VERSION
        rows = await again.execute("SELECT total_price FROM super_chats")
        assert int(rows[0]["total_price"]) == 50_000
    finally:
        await again.close()


@pytest.mark.asyncio
async def test_build_schema_sql_contains_v10_tables() -> None:
    """建库 DDL 含 v10 新形态（guards / viewer_facts / viewer_profiles / 画像复合键）。"""
    from src.modules.storage.schema import _GIFTS_SQL

    sql = build_schema_sql()
    for marker in (
        "CREATE TABLE IF NOT EXISTS guards",
        "CREATE TABLE IF NOT EXISTS viewer_facts",
        "CREATE TABLE IF NOT EXISTS viewer_profiles",
        "UNIQUE(platform, user_id)",
    ):
        assert marker in sql, f"DDL 缺少 {marker}"
    # gifts 表段内已改名 quantity（viewers 统计列 gift_count 保留，属不同语义）
    assert "gift_count" not in _GIFTS_SQL, "gifts 表应已改名 quantity"
    assert "_memory_facts" not in sql, "扁平事实私有表应已从 DDL 移除"
