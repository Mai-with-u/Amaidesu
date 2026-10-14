"""v12 迁移：思考 token 观测列、原始 usage 兜底列、client_type 正名为 profile_name"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.modules.llm.payload import Response, Usage
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.migrations.v12_llm_observation_and_profile_rename import migrate as v12_migrate
from src.modules.storage.schema import SCHEMA_VERSION
from tests.modules.storage.helpers import make_llm_manager

# v11 形状的两表 DDL（无 v12 新列；client_type 列名未改）
_V11_LLM_TABLES_SQL = """
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
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL);
"""


def _column_names(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _assert_v12_columns(conn: sqlite3.Connection) -> None:
    req_cols = _column_names(conn, "llm_requests")
    assert "reasoning_tokens" in _column_names(conn, "llm_usage")
    assert {"reasoning_tokens", "usage_raw_json", "profile_name"} <= req_cols
    assert "client_type" not in req_cols


def test_v12_migrate_is_idempotent_directly() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_V11_LLM_TABLES_SQL)
    for _ in range(3):
        v12_migrate(conn)
    _assert_v12_columns(conn)


async def test_fresh_db_has_v12_columns_and_profile_index(store: SQLiteDatabase) -> None:
    def _check() -> set:
        with store.manager.transaction() as conn:
            _assert_v12_columns(conn)
            return {row[1] for row in conn.execute("PRAGMA index_list(llm_requests)").fetchall()}

    assert "idx_llm_requests_profile" in await store._run_in_executor(_check)


async def test_v11_db_upgrade_renames_column_and_keeps_legacy_values(db_path: Path) -> None:
    """旧值域 llm/llm_fast/vlm 原样保留（不映射），dashboard 按字面筛选仍可命中。"""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_V11_LLM_TABLES_SQL)
    conn.execute("INSERT INTO schema_migrations VALUES (11, 0)")
    conn.execute(
        "INSERT INTO llm_usage(model_name, provider_name, request_type, prompt_tokens, completion_tokens,"
        " total_tokens, cache_hit_tokens, cache_miss_tokens, cost, duration_ms, timestamp_ms)"
        " VALUES ('old-model', 'p', 'generate', 10, 5, 15, 3, 7, 0.5, 100, 1000)"
    )
    for ts, client_type in ((2000, "llm"), (3000, "llm_fast"), (4000, "vlm")):
        conn.execute(
            "INSERT INTO llm_requests(request_id, timestamp_ms, client_type) VALUES (?, ?, ?)",
            (f"req-{client_type}", ts, client_type),
        )
    conn.commit()
    conn.close()

    for _ in range(2):  # 再次初始化幂等
        db = SQLiteDatabase(db_path)
        await db.initialize()
        try:
            assert await db.get_schema_version() == SCHEMA_VERSION
            rows = await db.execute(
                "SELECT profile_name, reasoning_tokens, usage_raw_json FROM llm_requests ORDER BY timestamp_ms"
            )
            assert [(r["profile_name"], r["reasoning_tokens"], r["usage_raw_json"]) for r in rows] == [
                ("llm", 0, None),
                ("llm_fast", 0, None),
                ("vlm", 0, None),
            ]
            assert [r["reasoning_tokens"] for r in await db.execute("SELECT reasoning_tokens FROM llm_usage")] == [0]
        finally:
            await db.close()


class _FakeReasoningClient:
    """厂商适配端替身：成功返回带 reasoning_tokens 的 usage，不发网络请求。"""

    def __init__(self, config) -> None:
        self.config = config

    async def generate(self, request, *, model: str, **kwargs) -> Response:
        raw = {"prompt_tokens": 10, "completion_tokens": 5, "completion_tokens_details": {"reasoning_tokens": 7}}
        return Response(
            success=True,
            content="回复",
            model="glm-4.7",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15, reasoning_tokens=7),
            usage_raw_json=json.dumps(raw),
        )


async def test_engine_persists_reasoning_tokens_without_raw_usage_body(store: SQLiteDatabase) -> None:
    """engine 成功调用 → 两表 reasoning_tokens 落库；usage_raw_json 正文出库到诊断文件，库列为 NULL。"""
    manager = make_llm_manager(_FakeReasoningClient({"name": "fake"}), llm_repo=store.llm)

    assert (await manager.generate("你好", profile="planner")).success
    assert [r["reasoning_tokens"] for r in await store.execute("SELECT reasoning_tokens FROM llm_usage")] == [7]
    rows = await store.execute("SELECT profile_name, reasoning_tokens, usage_raw_json FROM llm_requests")
    assert [(r["profile_name"], r["reasoning_tokens"], r["usage_raw_json"]) for r in rows] == [("planner", 7, None)]
