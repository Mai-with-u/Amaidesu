"""llm_usage / llm_requests 落库链路：仓储往返、LLMManager 旁路落库与降级、上下文解剖出库"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.modules.diagnostics.readers import read_latest_breakdowns
from src.modules.diagnostics.writer import DiagnosticsWriter
from src.modules.llm.payload import Response, Usage
from src.modules.storage.database import SQLiteDatabase
from tests.modules.storage.helpers import make_llm_manager

_SECTION_KEYS = {"messages", "mcp_tools", "system_tools", "skills", "system"}


class _FakeUsageClient:
    async def generate(self, request, **kwargs) -> Response:
        usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        return Response(success=True, content="回复", model="glm-4.7", usage=usage)


async def test_insert_llm_usage_roundtrip(store: SQLiteDatabase) -> None:
    rowid = await store.llm.insert_llm_usage(
        model_name="glm-4.7",
        provider_name="zhipu",
        request_type="chat",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cache_hit_tokens=80,
        cache_miss_tokens=20,
        cost=0.0123,
        duration_ms=850,
        profile_name="llm",
        timestamp_ms=1_700_000_000_000,
    )
    row = (await store.execute("SELECT * FROM llm_usage WHERE id=?", (rowid,)))[0]
    expected = {
        "model_name": "glm-4.7",
        "provider_name": "zhipu",
        "profile_name": "llm",
        "request_type": "chat",
        "prompt_tokens": 100,
        "total_tokens": 150,
        "cache_hit_tokens": 80,
        "cache_miss_tokens": 20,
        "duration_ms": 850,
        "timestamp_ms": 1_700_000_000_000,
        "assign_name": None,
        "live_session_id": None,
    }
    assert {k: row[k] for k in expected} == expected
    assert row["cost"] == pytest.approx(0.0123)


async def test_successful_call_persists_llm_usage(store: SQLiteDatabase) -> None:
    manager = make_llm_manager(_FakeUsageClient(), llm_repo=store.llm)
    assert (await manager.generate("你好", profile="planner")).success

    (row,) = await store.execute("SELECT * FROM llm_usage")
    assert (row["model_name"], row["provider_name"], row["profile_name"]) == ("glm-4.7", "fake", "planner")
    assert (row["prompt_tokens"], row["total_tokens"]) == (10, 15) and row["duration_ms"] >= 0


async def test_missing_or_failing_repo_does_not_break_call(store: SQLiteDatabase, monkeypatch) -> None:
    assert (await make_llm_manager(_FakeUsageClient()).generate("你好", profile="planner")).success

    async def _boom(**kwargs):
        raise RuntimeError("db locked")

    manager = make_llm_manager(_FakeUsageClient(), llm_repo=store.llm)
    monkeypatch.setattr(manager._observer._llm_repo, "insert_llm_usage", _boom)
    assert (await manager.generate("你好", profile="planner")).success


async def test_breakdown_goes_to_diagnostics(store: SQLiteDatabase, tmp_path: Path) -> None:
    """breakdown 不落库（列为 NULL），正文与分段占用走诊断小时流与 latest 快照。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    manager = make_llm_manager(_FakeUsageClient(), llm_repo=store.llm, diagnostics_writer=writer)
    result = await manager.generate("你好", profile="planner")
    assert result.success
    writer.stop()

    cols = {row["name"] for row in await store.execute("PRAGMA table_info(llm_requests)")}
    assert "breakdown_json" not in cols  # v17 起死列退役，分段占用只走诊断文件
    (stream,) = list((tmp_path / "llm_requests").rglob("*.jsonl"))
    record = json.loads(stream.read_text(encoding="utf-8").strip())
    breakdown = record["breakdown"]
    assert record["request_id"] == breakdown["request_id"] == result.request_id
    assert (breakdown["api_prompt_tokens"], breakdown["calibrated"]) == (10, True)
    assert {section["key"] for section in breakdown["sections"]} == _SECTION_KEYS
    assert isinstance(record["timestamp_ms"], int)
    assert read_latest_breakdowns(base_dir=tmp_path)["glm-4.7"]["request_id"] == result.request_id


async def test_breakdown_not_persisted_diagnostics_is_authoritative(store: SQLiteDatabase, tmp_path: Path) -> None:
    """分段占用不落库（列已随 v17 退役）：诊断小时流与 latest 快照是唯一数据源。"""
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    manager = make_llm_manager(_FakeUsageClient(), llm_repo=store.llm, diagnostics_writer=writer)
    result = await manager.generate("你好", profile="planner")
    writer.stop()

    cols = {row["name"] for row in await store.execute("PRAGMA table_info(llm_requests)")}
    assert "breakdown_json" not in cols
    (stream,) = list((tmp_path / "llm_requests").rglob("*.jsonl"))
    record = json.loads(stream.read_text(encoding="utf-8").strip())
    assert record["breakdown"]["request_id"] == result.request_id
    assert read_latest_breakdowns(base_dir=tmp_path)["glm-4.7"]["request_id"] == result.request_id
