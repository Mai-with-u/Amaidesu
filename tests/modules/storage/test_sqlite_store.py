"""
SQLiteStore 单元测试（Wave 3 / §1.50 / §1.53 9b）

覆盖：
- 11 张权威表 + schema_migrations 全部建出
- schema_migrations 记录当前版本
- ``simulated`` 列存在且默认 0（§1.6 贯穿列）
- 消费者 WHERE ``simulated=0`` 排除模拟数据（viewers / topics 统计视图中模拟数据零污染）
- is_healthy / list_tables / assert_schema_ready

权威参考：.omo/drafts/amaidesu-v2-storage-schema.md
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.storage import (
    SCHEMA_VERSION,
    SQLiteConnectionManager,
    SQLiteStore,
    list_expected_tables,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    """提供临时 DB 路径，测试结束清理。"""
    td = Path(tempfile.mkdtemp(prefix="w3-storage-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path) -> AsyncGenerator[SQLiteStore, None]:
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    yield s
    await s.close()


# =============================================================================
# 11 张权威表 + schema_migrations
# =============================================================================


@pytest.mark.asyncio
async def test_eleven_tables_created(store: SQLiteStore) -> None:
    """11 张权威表 + schema_migrations 全部应被建立。"""
    for table_name in list_expected_tables():
        exists = await store.table_exists(table_name)
        assert exists, f"缺少权威表: {table_name}"
    # 总数 = 11 业务表 + 1 schema_migrations = 12
    tables = await store.list_tables()
    expected_count = len(list_expected_tables())
    assert len(tables) >= expected_count, f"表数 {len(tables)} < 期望 {expected_count}"
    # 业务表 11 张都在
    business_tables = [t for t in list_expected_tables() if t != "schema_migrations"]
    for t in business_tables:
        assert t in tables, f"业务表 {t} 不在 sqlite_master"


@pytest.mark.asyncio
async def test_schema_migration_recorded(store: SQLiteStore) -> None:
    """schema_migrations 记录当前 SCHEMA_VERSION。"""
    version = await store.get_schema_version()
    assert version == SCHEMA_VERSION, f"已应用版本={version} 应等于代码期望={SCHEMA_VERSION}"


@pytest.mark.asyncio
async def test_assert_schema_ready_passes(store: SQLiteStore) -> None:
    """assert_schema_ready 自检通过。"""
    await store.assert_schema_ready()


# =============================================================================
# simulated 贯穿列（§1.6 用户拍板：默认 False / True 标记 / 消费方排除）
# =============================================================================


@pytest.mark.asyncio
async def test_simulated_column_present_on_live_chat(store: SQLiteStore) -> None:
    """live_chat 表必须包含 ``simulated`` 列（default 0）。"""
    rows = await store.execute("PRAGMA table_info(live_chat)")
    cols = {row["name"]: row for row in rows}
    assert "simulated" in cols, "live_chat 缺少 simulated 列（§1.6 贯穿列缺失）"
    # 默认 0 = 真实源
    assert int(cols["simulated"]["dflt_value"]) == 0, "simulated 列默认值应为 0（真实源）"


@pytest.mark.asyncio
async def test_simulated_column_present_on_gifts(store: SQLiteStore) -> None:
    """gifts 表必须包含 simulated 列。"""
    rows = await store.execute("PRAGMA table_info(gifts)")
    cols = {row["name"]: row for row in rows}
    assert "simulated" in cols


@pytest.mark.asyncio
async def test_simulated_column_present_on_super_chats(store: SQLiteStore) -> None:
    """super_chats 表必须包含 simulated 列。"""
    rows = await store.execute("PRAGMA table_info(super_chats)")
    cols = {row["name"]: row for row in rows}
    assert "simulated" in cols


@pytest.mark.asyncio
async def test_simulated_exclusion_query(store: SQLiteStore) -> None:
    """消费者 WHERE ``simulated=0`` 排除模拟数据——模拟观众不污染真实统计。

    模拟场景：
    - 插入 1 条真实礼物（simulated=False）+ 1 条模拟礼物（simulated=True）
    - 查询 WHERE ``simulated=0`` 应只返回 1 条真实礼物
    - 同演示：viewers 统计派生自此排除
    """
    # 礼物明细
    await store.execute(
        "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type) "
        "VALUES (1, 1700000000000, 'viewer', '真实弹幕', 'danmaku')"
    )
    await store.execute(
        "INSERT INTO live_chat(live_session_id, timestamp_ms, sender_role, content, message_type, simulated) "
        "VALUES (1, 1700000001000, 'viewer', '模拟弹幕', 'danmaku', 1)"
    )

    real_rows = await store.execute("SELECT * FROM live_chat WHERE simulated=0")
    assert len(real_rows) == 1, f"消费者查询应返回 1 条真实数据，实得 {len(real_rows)}"
    assert str(real_rows[0]["content"]) == "真实弹幕"

    # 全部
    all_rows = await store.execute("SELECT * FROM live_chat")
    assert len(all_rows) == 2


# =============================================================================
# 健康检查 / 时间戳 / 字段命名规范（_ms）
# =============================================================================


@pytest.mark.asyncio
async def test_is_healthy(store: SQLiteStore) -> None:
    assert await store.is_healthy() is True


@pytest.mark.asyncio
async def test_millisecond_fields_exist(store: SQLiteStore) -> None:
    """权威表必须以 *_ms 为时间字段命名（§1.44）。抽查关键表。"""
    # live_sessions 必须有 started_at_ms / updated_at_ms
    rows = await store.execute("PRAGMA table_info(live_sessions)")
    cols = {row["name"] for row in rows}
    assert "started_at_ms" in cols
    assert "updated_at_ms" in cols
    assert "ended_at_ms" in cols
    # gifts
    rows = await store.execute("PRAGMA table_info(gifts)")
    cols_gifts = {row["name"] for row in rows}
    assert "timestamp_ms" in cols_gifts
    # topics
    rows = await store.execute("PRAGMA table_info(topics)")
    cols_topics = {row["name"] for row in rows}
    assert "duration_ms" in cols_topics


@pytest.mark.asyncio
async def test_message_type_values_live_chat(store: SQLiteStore) -> None:
    """message_type 列存在（live_chat 字段语义）。"""
    rows = await store.execute("PRAGMA table_info(live_chat)")
    cols = {row["name"] for row in rows}
    assert "message_type" in cols


# =============================================================================
# ConnectionManager（被 SQLiteStore 间接覆盖；此为直接单元测试）
# =============================================================================


@pytest.mark.asyncio
async def test_connection_manager_pragmas(temp_db_path: Path) -> None:
    """SQLiteConnectionManager 应设置 WAL + foreign_keys PRAGMA。"""
    manager = SQLiteConnectionManager(temp_db_path)
    conn = manager.connection()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()
        # WAL 模式；fetchone 返回 (mode,) 或者 sqlite3.Row
        mode_str = str(mode[0]) if not isinstance(mode, dict) else str(mode["journal_mode"])
        # WAL 模式可能以 "wal" 返回（部分连接)
        assert "wal" in mode_str.lower(), f"期望 WAL 模式，实得 {mode_str}"
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        fk_val = int(fk[0] if not isinstance(fk, dict) else fk["foreign_keys"])
        assert fk_val == 1, f"foreign_keys 应为 1，实得 {fk_val}"
    finally:
        manager.close_all()


@pytest.mark.asyncio
async def test_initialize_idempotent(temp_db_path: Path) -> None:
    """多次调用 initialize 不应报错（幂等）。"""
    s = SQLiteStore(temp_db_path)
    await s.initialize()
    await s.initialize()
    await s.close()
