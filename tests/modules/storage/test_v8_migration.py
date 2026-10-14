"""v8 迁移：llm_usage.request_id 连接键 + llm_requests 缓存列；两表同事务原子写入"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.modules.llm.observation import record_call
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v8_llm_request_link import migrate as v8_migrate
from src.modules.storage.repos.llm import LLMRequestInsert, LLMUsageInsert
from src.modules.storage.schema import SCHEMA_VERSION

# v7 形状的两表 DDL（无 v8 新列）
_V7_LLM_TABLES_SQL = """
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
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    timestamp_ms INTEGER NOT NULL
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
    cost REAL NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL);
"""


def _assert_v8_columns(conn: sqlite3.Connection) -> None:
    def cols(table: str) -> set:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    assert "request_id" in cols("llm_usage")
    assert {"cache_hit_tokens", "cache_miss_tokens"} <= cols("llm_requests")


def test_v8_migrate_is_idempotent_directly() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_V7_LLM_TABLES_SQL)
    for _ in range(3):
        v8_migrate(conn)
    _assert_v8_columns(conn)


async def test_fresh_db_has_v8_columns(store: SQLiteDatabase) -> None:
    def _check() -> None:
        with store.manager.transaction() as conn:
            _assert_v8_columns(conn)

    await store._run_in_executor(_check)


async def test_v7_db_upgrade_keeps_rows_and_defaults(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_V7_LLM_TABLES_SQL)
    conn.execute("INSERT INTO schema_migrations VALUES (7, 0)")
    conn.execute(
        "INSERT INTO llm_usage(model_name, provider_name, request_type, prompt_tokens, completion_tokens,"
        " total_tokens, cache_hit_tokens, cache_miss_tokens, cost, duration_ms, timestamp_ms)"
        " VALUES ('old-model', 'p', 'generate', 10, 5, 15, 3, 7, 0.5, 100, 1000)"
    )
    conn.execute("INSERT INTO llm_requests(request_id, timestamp_ms, model_name) VALUES ('old-req', 2000, 'old-model')")
    conn.commit()
    conn.close()

    for _ in range(2):  # 再次初始化幂等
        db = SQLiteDatabase(db_path)
        await db.initialize()
        try:
            assert await db.get_schema_version() == SCHEMA_VERSION
            # 存量行：连接键为 NULL，缓存列补默认 0
            usage = await db.execute("SELECT model_name, cache_hit_tokens, request_id FROM llm_usage")
            assert [tuple(r) for r in usage] == [("old-model", 3, None)]
            requests = await db.execute("SELECT request_id, cache_hit_tokens, cache_miss_tokens FROM llm_requests")
            assert [tuple(r) for r in requests] == [("old-req", 0, 0)]
        finally:
            await db.close()


def _usage(request_id: str, **kwargs) -> LLMUsageInsert:
    return LLMUsageInsert(
        model_name="m1",
        provider_name="p",
        request_type="generate",
        prompt_tokens=64,
        completion_tokens=8,
        total_tokens=72,
        request_id=request_id,
        **kwargs,
    )


async def test_record_call_is_atomic_across_tables(store: SQLiteDatabase) -> None:
    """第二步（llm_requests）失败 → 两表均无新行。"""
    await store.execute(
        "CREATE TRIGGER boom_requests BEFORE INSERT ON llm_requests BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        await record_call(
            store.llm,
            usage=_usage("req-atomic"),
            request=LLMRequestInsert(request_id="req-atomic", timestamp_ms=1000, model_name="m1"),
        )
    for table in ("llm_usage", "llm_requests"):
        assert (await store.execute(f"SELECT COUNT(*) AS n FROM {table}"))[0]["n"] == 0


async def test_usage_and_request_join_on_request_id(store: SQLiteDatabase) -> None:
    await record_call(
        store.llm,
        usage=_usage("req-join-1", cache_hit_tokens=50, cache_miss_tokens=14, timestamp_ms=1000),
        request=LLMRequestInsert(request_id="req-join-1", timestamp_ms=1000, model_name="m1", prompt_tokens=64),
    )
    rows = await store.execute(
        "SELECT u.request_id, u.cache_hit_tokens AS u_hit, r.cache_hit_tokens AS r_hit, r.model_name"
        " FROM llm_usage u JOIN llm_requests r ON u.request_id = r.request_id"
    )
    assert [tuple(r) for r in rows] == [("req-join-1", 50, 0, "m1")]
