"""rundowns 表 CRUD 与 v5 → v6 迁移测试。

覆盖：
- 存储层 CRUD 往返（``upsert_rundown`` / ``get_rundown`` / ``list_rundowns`` / ``delete_rundown``）
- ``created_at_ms`` 首次写入后保留；``updated_at_ms`` 每次刷新
- 列表按 ``created_at_ms`` 升序（先建先出）
- v5 形状的旧库升级：``agenda_plan`` / ``agenda_runtime`` 被 DROP；``rundowns`` 表创建
- 迁移幂等：再次 ``initialize()`` 不重复建表、不破坏数据
- 新建库直接走到 v6：``rundowns`` 在期望表清单内，``agenda_*`` 不在
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.agents.streamer.rundown.rundown import (
    DEFAULT_RUNDOWN,
    Rundown,
    RundownSegment,
)
from src.modules.storage import SCHEMA_VERSION, list_expected_tables
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.schema import build_schema_sql
from src.modules.time_utils import now_ms


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="rundowns-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path):
    """直接使用 SQLiteDatabase：断言/自检走 db，流程单读写走 db.rundowns。"""
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()


def _make_rundown(
    *,
    rundown_id: str,
    title: str = "测试流程单",
    segments: list[RundownSegment] | None = None,
) -> Rundown:
    """造一份流程单（segments 缺省时给一个最小合规环节）。"""
    if segments is None:
        segments = [
            RundownSegment(
                id="s1",
                title="开场",
                task_description="打招呼",
                expected_ms=10_000,
            ),
        ]
    return Rundown(rundown_id=rundown_id, title=title, segments=segments)


# =============================================================================
# 建表自检
# =============================================================================


@pytest.mark.asyncio
async def test_rundowns_table_exists_after_initialize(store: SQLiteDatabase) -> None:
    """``store.initialize()`` 后 ``rundowns`` 表存在；``agenda_*`` 不在。"""
    assert await store.table_exists("rundowns")
    assert not await store.table_exists("agenda_plan")
    assert not await store.table_exists("agenda_runtime")
    assert "rundowns" in list_expected_tables()
    assert "agenda_plan" not in list_expected_tables()
    assert "agenda_runtime" not in list_expected_tables()


# =============================================================================
# CRUD 往返
# =============================================================================


@pytest.mark.asyncio
async def test_rundown_round_trip(store: SQLiteDatabase) -> None:
    """upsert → get → list → delete 全链。"""
    rundown = _make_rundown(rundown_id="rd_1", title="首播流程单")

    await store.rundowns.upsert_rundown(rundown)

    # get 命中
    fetched = await store.rundowns.get_rundown("rd_1")
    assert fetched is not None
    assert fetched.rundown_id == "rd_1"
    assert fetched.title == "首播流程单"
    assert len(fetched.segments) == 1
    assert fetched.segments[0].id == "s1"
    assert fetched.segments[0].expected_ms == 10_000

    # list 含 1 条
    listed = await store.rundowns.list_rundowns()
    assert [r.rundown_id for r in listed] == ["rd_1"]

    # delete 命中
    assert await store.rundowns.delete_rundown("rd_1") is True
    # 再删返回 False
    assert await store.rundowns.delete_rundown("rd_1") is False
    # get 未命中
    assert await store.rundowns.get_rundown("rd_1") is None
    # list 为空
    assert await store.rundowns.list_rundowns() == []


@pytest.mark.asyncio
async def test_rundown_upsert_preserves_created_at_updates_only_updated_at(
    store: SQLiteDatabase,
) -> None:
    """``upsert_rundown`` 二次写入保留 ``created_at_ms``，刷新 ``updated_at_ms``。"""
    initial = _make_rundown(rundown_id="rd_1")
    await store.rundowns.upsert_rundown(initial)
    rows = await store.execute("SELECT created_at_ms, updated_at_ms FROM rundowns WHERE id=?", ("rd_1",))
    first_row = rows[0]
    first_created = int(first_row["created_at_ms"])
    first_updated = int(first_row["updated_at_ms"])

    # 跨毫秒边界两次写入（确保 updated_at_ms 可见差异）
    before_ms = now_ms()
    while now_ms() <= first_updated:
        # 自旋直到时钟推进，避免快机器同毫秒
        pass

    updated = Rundown(
        rundown_id="rd_1",
        title="更新后的标题",
        segments=[
            RundownSegment(
                id="s1",
                title="开场（已改）",
                task_description="打招呼",
                expected_ms=20_000,
            ),
        ],
    )
    await store.rundowns.upsert_rundown(updated)
    rows2 = await store.execute("SELECT created_at_ms, updated_at_ms FROM rundowns WHERE id=?", ("rd_1",))
    second_row = rows2[0]
    second_created = int(second_row["created_at_ms"])
    second_updated = int(second_row["updated_at_ms"])

    # created_at_ms 不变
    assert second_created == first_created
    # updated_at_ms 推进
    assert second_updated > first_updated
    assert before_ms <= second_updated
    # 业务字段更新
    fetched = await store.rundowns.get_rundown("rd_1")
    assert fetched is not None
    assert fetched.title == "更新后的标题"
    assert fetched.segments[0].title == "开场（已改）"
    assert fetched.segments[0].expected_ms == 20_000


@pytest.mark.asyncio
async def test_rundown_list_ordered_by_created_at_asc(store: SQLiteDatabase) -> None:
    """``list_rundowns`` 按 ``created_at_ms`` 升序返回。"""
    r1 = _make_rundown(rundown_id="rd_1")
    await store.rundowns.upsert_rundown(r1)
    r2 = _make_rundown(rundown_id="rd_2")
    await store.rundowns.upsert_rundown(r2)
    r3 = _make_rundown(rundown_id="rd_3")
    await store.rundowns.upsert_rundown(r3)

    listed = await store.rundowns.list_rundowns()
    assert [r.rundown_id for r in listed] == ["rd_1", "rd_2", "rd_3"]


@pytest.mark.asyncio
async def test_rundown_get_unknown_returns_none(store: SQLiteDatabase) -> None:
    """``get_rundown`` 未命中返回 ``None``。"""
    assert await store.rundowns.get_rundown("ghost") is None


@pytest.mark.asyncio
async def test_rundown_segments_serialization_round_trip(store: SQLiteDatabase) -> None:
    """复杂 segments（含 ``key_points`` / ``min_duration_ms`` / ``notes``）往返无损。"""
    rundown = Rundown(
        rundown_id="rd_complex",
        title="复杂流程单",
        segments=[
            RundownSegment(
                id="s1",
                title="开场",
                task_description="打招呼",
                key_points=["问好", "自我介绍"],
                expected_ms=60_000,
                min_duration_ms=30_000,
                notes="参考开场白：大家好，欢迎来到直播间",
            ),
            RundownSegment(
                id="s2",
                title="聊天",
                task_description="和观众互动",
                expected_ms=300_000,
            ),
        ],
    )
    await store.rundowns.upsert_rundown(rundown)
    fetched = await store.rundowns.get_rundown("rd_complex")
    assert fetched is not None
    assert len(fetched.segments) == 2
    s1 = fetched.segments[0]
    assert s1.key_points == ["问好", "自我介绍"]
    assert s1.min_duration_ms == 30_000
    assert s1.notes == "参考开场白：大家好，欢迎来到直播间"
    assert fetched.segments[1].min_duration_ms is None
    assert fetched.segments[1].notes is None


@pytest.mark.asyncio
async def test_rundown_default_rundown_upserts_and_round_trips(store: SQLiteDatabase) -> None:
    """``DEFAULT_RUNDOWN`` 落库 / 读回无损。"""
    await store.rundowns.upsert_rundown(DEFAULT_RUNDOWN)
    fetched = await store.rundowns.get_rundown(DEFAULT_RUNDOWN.rundown_id)
    assert fetched is not None
    assert fetched.rundown_id == DEFAULT_RUNDOWN.rundown_id
    assert fetched.title == DEFAULT_RUNDOWN.title
    assert len(fetched.segments) == len(DEFAULT_RUNDOWN.segments)
    for original, round_tripped in zip(DEFAULT_RUNDOWN.segments, fetched.segments, strict=True):
        assert round_tripped.id == original.id
        assert round_tripped.title == original.title
        assert round_tripped.task_description == original.task_description
        assert round_tripped.key_points == original.key_points
        assert round_tripped.expected_ms == original.expected_ms


# =============================================================================
# v5 → v6 迁移
# =============================================================================


@pytest.mark.asyncio
async def test_v5_to_v6_migration_drops_agenda_and_creates_rundowns(temp_db_path: Path) -> None:
    """v5 形状的旧库升级：``agenda_plan`` / ``agenda_runtime`` 被 DROP，``rundowns`` 表创建。"""
    conn = sqlite3.connect(str(temp_db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE agenda_plan (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                live_session_id  INTEGER NOT NULL,
                label            TEXT NOT NULL,
                "order"          INTEGER NOT NULL,
                starts_at_ms     INTEGER NOT NULL,
                expected_ms      INTEGER NOT NULL,
                note             TEXT,
                created_by       TEXT NOT NULL
            );
            CREATE TABLE agenda_runtime (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                live_session_id  INTEGER NOT NULL,
                plan_id          INTEGER,
                label            TEXT NOT NULL,
                "order"          INTEGER NOT NULL,
                starts_at_ms     INTEGER NOT NULL,
                expected_ms      INTEGER NOT NULL,
                done             INTEGER NOT NULL DEFAULT 0,
                current          INTEGER NOT NULL DEFAULT 0,
                note             TEXT,
                inserted_by      TEXT NOT NULL
            );
            CREATE TABLE schema_migrations (
                version        INTEGER PRIMARY KEY,
                applied_at_ms  INTEGER NOT NULL
            );
            INSERT INTO schema_migrations(version, applied_at_ms) VALUES (5, 0);
            """
        )
        conn.commit()
    finally:
        conn.close()

    s = SQLiteDatabase(temp_db_path)
    try:
        await s.initialize()
        assert await s.get_schema_version() == SCHEMA_VERSION

        # 旧表已 DROP
        assert not await s.table_exists("agenda_plan")
        assert not await s.table_exists("agenda_runtime")
        # rundowns 表已 CREATE
        assert await s.table_exists("rundowns")

        # rundowns 表 schema 合规（列名 / 类型 / 主键）
        cols = await s.execute("PRAGMA table_info(rundowns)")
        col_map = {row["name"]: row for row in cols}
        assert col_map["id"]["type"] == "TEXT" and int(col_map["id"]["pk"]) == 1
        assert col_map["title"]["type"] == "TEXT" and int(col_map["title"]["notnull"]) == 1
        assert col_map["segments_json"]["type"] == "TEXT"
        assert int(col_map["segments_json"]["notnull"]) == 1
        assert col_map["created_at_ms"]["type"] == "INTEGER"
        assert int(col_map["created_at_ms"]["notnull"]) == 1
        assert col_map["updated_at_ms"]["type"] == "INTEGER"
        assert int(col_map["updated_at_ms"]["notnull"]) == 1

        # 迁移后 rundowns 表可写可读
        await s.rundowns.upsert_rundown(_make_rundown(rundown_id="post_migrate"))
        listed = await s.rundowns.list_rundowns()
        assert [r.rundown_id for r in listed] == ["post_migrate"]
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_v6_migration_is_idempotent(temp_db_path: Path) -> None:
    """v5 → v6 迁移幂等：再次 ``initialize()`` 不重复迁移、不破坏数据。"""
    # 旧库（v5）创建
    conn = sqlite3.connect(str(temp_db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE agenda_plan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                live_session_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                "order" INTEGER NOT NULL,
                starts_at_ms INTEGER NOT NULL,
                expected_ms INTEGER NOT NULL,
                note TEXT,
                created_by TEXT NOT NULL
            );
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at_ms INTEGER NOT NULL
            );
            INSERT INTO schema_migrations(version, applied_at_ms) VALUES (5, 0);
            """
        )
        conn.commit()
    finally:
        conn.close()

    # 首次升级
    first = SQLiteDatabase(temp_db_path)
    await first.initialize()
    try:
        await first.rundowns.upsert_rundown(_make_rundown(rundown_id="keep_me"))
    finally:
        await first.close()

    # 再次打开 —— 不应破坏数据；migration 幂等
    second = SQLiteDatabase(temp_db_path)
    try:
        await second.initialize()
        assert await second.get_schema_version() == SCHEMA_VERSION
        # agenda 表依旧不存在
        assert not await second.table_exists("agenda_plan")
        # rundowns 表依旧可读
        fetched = await second.rundowns.get_rundown("keep_me")
        assert fetched is not None
        assert fetched.rundown_id == "keep_me"
        # schema_migrations 没有重复记录（INSERT OR IGNORE）
        rows = await second.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [int(r["version"]) for r in rows]
        assert versions == sorted(set(versions))
        assert SCHEMA_VERSION in versions
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_fresh_db_reaches_current_schema_version(temp_db_path: Path) -> None:
    """全新库首次 ``initialize()`` 直接走到当前 SCHEMA_VERSION；无 agenda 表。"""
    s = SQLiteDatabase(temp_db_path)
    try:
        await s.initialize()
        assert await s.get_schema_version() == SCHEMA_VERSION
        tables = await s.list_tables()
        # 期望表清单均已建立
        for t in list_expected_tables():
            assert t in tables, f"新建库缺少期望表: {t}"
        # agenda 表不在（已被移除出权威表清单）
        assert "agenda_plan" not in tables
        assert "agenda_runtime" not in tables
        # rundowns 表已建立
        assert "rundowns" in tables
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_build_schema_sql_contains_rundowns_not_agenda() -> None:
    """``build_schema_sql()`` 包含 ``rundowns`` DDL、不含 agenda DDL。"""
    sql = build_schema_sql()
    assert "CREATE TABLE IF NOT EXISTS rundowns" in sql
    assert "CREATE TABLE IF NOT EXISTS agenda_plan" not in sql
    assert "CREATE TABLE IF NOT EXISTS agenda_runtime" not in sql
