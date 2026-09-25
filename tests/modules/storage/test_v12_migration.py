"""v12 迁移单测：思考观测捕获 + 原始 usage 兜底 + 请求历史正名。

覆盖：

- 新库建表即含新列（llm_usage.reasoning_tokens / llm_requests.reasoning_tokens /
  llm_requests.usage_raw_json / llm_requests.profile_name 替代 client_type）
- v11 形状旧库升级：旧 client_type 列改名 profile_name、旧值原样保留、新
  列取默认值、版本推进、重复执行幂等
- llm_requests 新列索引（idx_llm_requests_profile）就位
- v12 数据落库端到端：经 engine 成功调用后 llm_usage + llm_requests 两表
  均含 reasoning_tokens；llm_requests 额外含 usage_raw_json 与
  profile_name（与 LLM 用途 profile 名一致）
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from src.modules.llm.bootstrap import _ResolvedModel, _ResolvedProfile
from src.modules.llm.engine import LLMManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.schema import SCHEMA_VERSION
from src.modules.storage.migrations.v12_llm_observation_and_profile_rename import migrate as v12_migrate

# v11 形状的两表 DDL（无 v12 新列；client_type 列名未改）
_V11_LLM_TABLES_SQL = """
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
    cache_hit_tokens   INTEGER NOT NULL,
    cache_miss_tokens  INTEGER NOT NULL,
    cost               REAL NOT NULL,
    duration_ms        INTEGER NOT NULL,
    timestamp_ms       INTEGER NOT NULL,
    request_id         TEXT
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
    cache_hit_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens   INTEGER NOT NULL DEFAULT 0,
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
    td = Path(tempfile.mkdtemp(prefix="v12-migration-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


def _column_names(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}  # noqa: S608 表名为代码内常量


def test_v12_migrate_is_idempotent_directly() -> None:
    """对 v11 形状（或已是 v12 形状）旧表重复执行 v12 回调均安全。"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_V11_LLM_TABLES_SQL)
    v12_migrate(conn)
    v12_migrate(conn)
    v12_migrate(conn)
    usage_cols = _column_names(conn, "llm_usage")
    req_cols = _column_names(conn, "llm_requests")
    assert "reasoning_tokens" in usage_cols
    assert "reasoning_tokens" in req_cols
    assert "usage_raw_json" in req_cols
    assert "profile_name" in req_cols
    assert "client_type" not in req_cols


@pytest.mark.asyncio
async def test_fresh_db_has_v12_columns(temp_db_path: Path) -> None:
    """新库直建即含 v12 全套新列（含 client_type 已不存在、profile_name 就位）。"""
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION

        def _exec() -> tuple:
            with store.manager.transaction() as conn:
                usage_cols = _column_names(conn, "llm_usage")
                req_cols = _column_names(conn, "llm_requests")
                # profile_name 索引就位
                idx_rows = conn.execute("PRAGMA index_list(llm_requests)").fetchall()
                idx_names = {row[1] for row in idx_rows}
                return usage_cols, req_cols, idx_names

        usage_cols, req_cols, idx_names = await store._run_in_executor(_exec)
        assert "reasoning_tokens" in usage_cols
        assert "reasoning_tokens" in req_cols
        assert "usage_raw_json" in req_cols
        assert "profile_name" in req_cols
        assert "client_type" not in req_cols
        assert "idx_llm_requests_profile" in idx_names
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_v11_db_upgrades_renames_column_and_preserves_legacy_values(temp_db_path: Path) -> None:
    """v11 形状旧库升级：client_type 列改名 profile_name、旧值原样保留、版本推进、再次初始化幂等。

    旧值域 llm/llm_fast/llm_summary/vlm 按既定约定原样保留（不映射）——既不
    改写也不丢弃，dashboard 后续按字面筛选仍可命中。
    """
    conn = sqlite3.connect(str(temp_db_path))
    conn.executescript(_V11_LLM_TABLES_SQL)
    conn.execute("INSERT INTO schema_migrations(version, applied_at_ms) VALUES (11, 0)")
    # 旧值域：实测真实混杂形态（n≈3546 + n≈497），保留原样验证
    conn.execute(
        "INSERT INTO llm_usage(model_name, provider_name, request_type,"
        " prompt_tokens, completion_tokens, total_tokens, cache_hit_tokens, cache_miss_tokens,"
        " cost, duration_ms, timestamp_ms)"
        " VALUES ('old-model', 'p', 'generate', 10, 5, 15, 3, 7, 0.5, 100, 1000)"
    )
    conn.execute(
        "INSERT INTO llm_requests(request_id, timestamp_ms, client_type, model_name,"
        " prompt_tokens, total_tokens)"
        " VALUES ('req-old-llm', 2000, 'llm', 'old-model', 10, 15)"
    )
    conn.execute(
        "INSERT INTO llm_requests(request_id, timestamp_ms, client_type, model_name)"
        " VALUES ('req-old-llm-fast', 3000, 'llm_fast', 'old-model')"
    )
    conn.execute(
        "INSERT INTO llm_requests(request_id, timestamp_ms, client_type, model_name)"
        " VALUES ('req-old-vlm', 4000, 'vlm', 'old-model')"
    )
    conn.commit()
    conn.close()

    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    try:
        assert await store.get_schema_version() == SCHEMA_VERSION
        # 旧行 profile_name 列已改名，旧值原样保留
        rows = await store.execute(
            "SELECT request_id, profile_name FROM llm_requests ORDER BY timestamp_ms"
        )
        profile_by_req = {r["request_id"]: r["profile_name"] for r in rows}
        assert profile_by_req == {
            "req-old-llm": "llm",
            "req-old-llm-fast": "llm_fast",
            "req-old-vlm": "vlm",
        }
        # 新列默认值（reasoning_tokens = 0；usage_raw_json NULL）
        sample = await store.execute(
            "SELECT reasoning_tokens, usage_raw_json FROM llm_requests WHERE request_id = ?",
            ("req-old-llm",),
        )
        assert int(sample[0]["reasoning_tokens"]) == 0
        assert sample[0]["usage_raw_json"] is None
        # llm_usage.reasoning_tokens 也补默认 0
        usage_rows = await store.execute("SELECT reasoning_tokens FROM llm_usage")
        assert int(usage_rows[0]["reasoning_tokens"]) == 0
    finally:
        await store.close()

    # 幂等：再次 initialize 不报错、数据不重复、版本不回退
    again = SQLiteDatabase(temp_db_path)
    await again.initialize()
    try:
        assert await again.get_schema_version() == SCHEMA_VERSION
        n_req = await again.execute("SELECT COUNT(*) AS n FROM llm_requests")
        n_usage = await again.execute("SELECT COUNT(*) AS n FROM llm_usage")
        assert int(n_req[0]["n"]) == 3
        assert int(n_usage[0]["n"]) == 1
        # 旧值仍原样
        again_rows = await again.execute("SELECT profile_name FROM llm_requests")
        assert {r["profile_name"] for r in again_rows} == {"llm", "llm_fast", "vlm"}
    finally:
        await again.close()


class _FakeReasoningClient:
    """fake 厂商适配端：generate 成功并返回带 reasoning_tokens 的 usage（不发起网络请求）。"""

    def __init__(self, config):
        # 不调 super().__init__：fake 不需要 self.config 真实保存，避开 LSP 类型不匹配
        self.config = config

    async def generate(
        self,
        request,
        *,
        model: str,
        temperature=None,
        reasoning_effort=None,
        on_delta=None,
        interrupt_flag=None,
    ):
        from src.modules.llm.payload import Response as PayloadResponse, Usage as PayloadUsage

        # 同时设置 usage_raw_json：模拟真实厂商响应（OpenAI SDK 解析即弃链路）
        # 该兜底供 §2 决策第 2 条追溯重放使用，fake 模拟 SDK CompletionUsage 对象
        # 走相同的 json.dumps 路径
        fake_raw = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "completion_tokens_details": {"reasoning_tokens": 7},
        }
        return PayloadResponse(
            success=True,
            content="回复",
            model="glm-4.7",
            usage=PayloadUsage(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                reasoning_tokens=7,
            ),
            usage_raw_json=json.dumps(fake_raw, ensure_ascii=False),
        )


def _make_manager_with_fake_client(store) -> LLMManager:
    manager = LLMManager(llm_repo=store.llm)
    fake_client = _FakeReasoningClient({"name": "fake"})
    manager._provider_clients["fake"] = fake_client
    manager._providers["fake"] = ({"name": "fake", "client_type": "openai"}, fake_client)
    manager._models["glm-4.7"] = (
        {"name": "glm-4.7", "model_identifier": "glm-4.7", "api_provider": "fake"},
        "fake",
    )
    manager._profiles["planner"] = _ResolvedProfile(
        profile_name="planner",
        slow_threshold_ms=15_000,
        selection_strategy="sequential",
        seed=0,
        temperature=0.3,
        models=[_ResolvedModel(model_name="glm-4.7", model_identifier="glm-4.7", provider_name="fake")],
    )
    manager._model_call_counts["planner"] = {}
    return manager


@pytest.mark.asyncio
async def test_engine_persists_reasoning_tokens_and_usage_raw_json(temp_db_path: Path) -> None:
    """v12 端到端：engine 成功调用 → llm_usage + llm_requests 两表均含 reasoning_tokens；
    llm_requests 额外含 usage_raw_json（JSON 字符串）与 profile_name（用途 profile 名）。"""
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    try:
        manager = _make_manager_with_fake_client(store)
        result = await manager.generate("你好", profile="planner")
        assert result.success

        usage_rows = await store.execute("SELECT reasoning_tokens FROM llm_usage")
        assert int(usage_rows[0]["reasoning_tokens"]) == 7

        request_rows = await store.execute(
            "SELECT profile_name, reasoning_tokens, usage_raw_json FROM llm_requests"
        )
        assert len(request_rows) == 1
        row = request_rows[0]
        assert row["profile_name"] == "planner"
        assert int(row["reasoning_tokens"]) == 7
        # usage_raw_json 含 OpenAI 风格嵌套结构（解析即弃链路唯一兜底，保留厂商原始字段）
        raw = json.loads(row["usage_raw_json"]) if row["usage_raw_json"] else {}
        assert raw["prompt_tokens"] == 10
        assert raw["completion_tokens_details"]["reasoning_tokens"] == 7
    finally:
        await store.close()
