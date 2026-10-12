"""v8 迁移单测：连接键 + 请求历史缓存列。

覆盖：
- 新库建表即含新列（llm_usage.request_id / llm_requests 缓存两列）
- v7 形状旧库升级：旧行保留、新列取默认值、版本推进、重复执行幂等
- 两表同事务原子写入：第二步失败整体回滚
- llm_usage.request_id 与 llm_requests.request_id 可 join
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from src.modules.llm.observation import record_call
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.schema import SCHEMA_VERSION
from src.modules.storage.migrations.v8_llm_request_link import migrate as v8_migrate
from src.modules.storage.repos.llm import LLMRequestInsert, LLMUsageInsert

# v7 形状的两表 DDL（无 v8 新列），用于注入存量库
_V7_LLM_TABLES_SQL = """
CREATE TABLE llm_usage (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    live_session_id    INTEGER,
    model_name         TEXT NOT NULL,
    assign_name        TEXT,
    profile_name       TEXT,
    provider_name      TEXT NOT NULL,
    request_type       TEXT NOT NULL,
    prompt_tokens      INTEGER NOT NULL,
    completion_tokens  INTEGER NOT NULL,
    total_tokens       INTEGER NOT NULL,
    cache_hit_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens  INTEGER NOT NULL DEFAULT 0,
    cost               REAL NOT NULL,
    duration_ms        INTEGER NOT NULL,
    timestamp_ms       INTEGER NOT NULL
);
CREATE TABLE llm_requests (
    request_id          TEXT PRIMARY KEY,
    timestamp_ms        INTEGER NOT NULL,
    client_type         TEXT NOT NULL DEFAULT '',
    model_name          TEXT NOT NULL DEFAULT '',
    request_params      TEXT,
    response_content    TEXT,
    reasoning_content   TEXT,
    tool_calls          TEXT,
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    cost                REAL NOT NULL DEFAULT 0,
    success             INTEGER NOT NULL DEFAULT 1,
    error               TEXT,
    latency_ms          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE schema_migrations (
    version        INTEGER PRIMARY KEY,
    applied_at_ms  INTEGER NOT NULL
);
"""


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="v8-migration-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


def _column_names(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}  # noqa: S608 表名为代码内常量


def test_v8_migrate_is_idempotent_directly() -> None:
    """对已是 v8 形状（或全新空表缺失场景）重复执行回调均安全。"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_V7_LLM_TABLES_SQL)
    v8_migrate(conn)
    v8_migrate(conn)
    v8_migrate(conn)
    assert "request_id" in _column_names(conn, "llm_usage")
    assert {"cache_hit_tokens", "cache_miss_tokens"} <= _column_names(conn, "llm_requests")


@pytest.mark.asyncio
async def test_fresh_db_has_new_columns(temp_db_path: Path) -> None:
    """新库直建即含 v8 新列。"""
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION

        def _exec() -> tuple:
            with store.manager.transaction() as conn:
                return _column_names(conn, "llm_usage"), _column_names(conn, "llm_requests")

        usage_cols, request_cols = await store._run_in_executor(_exec)
        assert "request_id" in usage_cols
        assert {"cache_hit_tokens", "cache_miss_tokens"} <= request_cols
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_v7_db_upgrades_keeps_rows_and_defaults(temp_db_path: Path) -> None:
    """v7 形状旧库升级：旧行保留、新列默认值、版本推进、再次初始化幂等。"""
    conn = sqlite3.connect(str(temp_db_path))
    conn.executescript(_V7_LLM_TABLES_SQL)
    conn.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (7, 0)")
    conn.execute(
        "INSERT INTO llm_usage(model_name, provider_name, request_type,"
        " prompt_tokens, completion_tokens, total_tokens, cache_hit_tokens, cache_miss_tokens,"
        " cost, duration_ms, timestamp_ms)"
        " VALUES ('old-model', 'p', 'generate', 10, 5, 15, 3, 7, 0.5, 100, 1000)"
    )
    conn.execute(
        "INSERT INTO llm_requests(request_id, timestamp_ms, model_name, prompt_tokens, total_tokens)"
        " VALUES ('old-req', 2000, 'old-model', 10, 15)"
    )
    conn.commit()
    conn.close()

    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION
        usage_rows = await store.execute("SELECT * FROM llm_usage")
        assert len(usage_rows) == 1
        # 旧行保留；新增连接键列对存量行为 NULL
        assert usage_rows[0]["model_name"] == "old-model"
        assert usage_rows[0]["cache_hit_tokens"] == 3
        assert usage_rows[0]["request_id"] is None

        request_rows = await store.execute("SELECT * FROM llm_requests")
        assert len(request_rows) == 1
        assert request_rows[0]["request_id"] == "old-req"
        # 缓存列补默认 0（存量行没有缓存上报）
        assert request_rows[0]["cache_hit_tokens"] == 0
        assert request_rows[0]["cache_miss_tokens"] == 0
    finally:
        await store.close()

    # 幂等：再次 initialize 不报错、数据不重复、版本不回退
    again = SQLiteDatabase(temp_db_path)
    await again.initialize()
    try:
        assert await again.get_schema_version() == SCHEMA_VERSION
        n_usage = await again.execute("SELECT COUNT(*) AS n FROM llm_usage")
        n_request = await again.execute("SELECT COUNT(*) AS n FROM llm_requests")
        assert int(n_usage[0]["n"]) == 1
        assert int(n_request[0]["n"]) == 1
    finally:
        await again.close()


@pytest.mark.asyncio
async def test_two_table_atomic(temp_db_path: Path) -> None:
    """同事务第二步（llm_requests）注入失败 → 两表均无新行。"""
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    try:
        # 触发器让 llm_requests 的任何插入报错，模拟第二步失败
        await store.execute(
            "CREATE TRIGGER boom_requests BEFORE INSERT ON llm_requests"
            " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )

        with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
            await record_call(
                store.llm,
                usage=LLMUsageInsert(
                    model_name="m1",
                    provider_name="p",
                    request_type="generate",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    request_id="req-atomic",
                ),
                request=LLMRequestInsert(request_id="req-atomic", timestamp_ms=1000, model_name="m1"),
            )

        n_usage = await store.execute("SELECT COUNT(*) AS n FROM llm_usage")
        n_request = await store.execute("SELECT COUNT(*) AS n FROM llm_requests")
        assert int(n_usage[0]["n"]) == 0, "第一步 llm_usage 写入未随事务回滚"
        assert int(n_request[0]["n"]) == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_usage_request_join_on_request_id(temp_db_path: Path) -> None:
    """同 request_id 两行可 join（连接键生效）。"""
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    try:
        await record_call(
            store.llm,
            usage=LLMUsageInsert(
                model_name="m1",
                provider_name="p",
                request_type="generate",
                prompt_tokens=64,
                completion_tokens=8,
                total_tokens=72,
                cache_hit_tokens=50,
                cache_miss_tokens=14,
                request_id="req-join-1",
                timestamp_ms=1000,
            ),
            request=LLMRequestInsert(
                request_id="req-join-1",
                timestamp_ms=1000,
                model_name="m1",
                prompt_tokens=64,
                completion_tokens=8,
                total_tokens=72,
                request_params_json=json.dumps({"messages": []}, ensure_ascii=False),
            ),
        )
        rows = await store.execute(
            "SELECT u.request_id AS rid, u.cache_hit_tokens AS u_hit,"
            " r.cache_hit_tokens AS r_hit, r.model_name AS r_model"
            " FROM llm_usage u JOIN llm_requests r ON u.request_id = r.request_id"
        )
        assert len(rows) == 1
        assert rows[0]["rid"] == "req-join-1"
        assert rows[0]["u_hit"] == 50
        assert rows[0]["r_hit"] == 0
        assert rows[0]["r_model"] == "m1"
    finally:
        await store.close()
