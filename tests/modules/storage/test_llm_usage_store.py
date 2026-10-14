"""llm_usage / llm_requests 落库链路：仓储往返、LLMManager 旁路落库与降级、上下文解剖出库与事件"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.modules.diagnostics.readers import read_latest_breakdowns
from src.modules.diagnostics.writer import DiagnosticsWriter
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import LLMContextUsedPayload
from src.modules.llm.payload import Response, Usage
from src.modules.storage.database import SQLiteDatabase
from tests.modules.storage.helpers import make_llm_manager

_SECTION_KEYS = {"messages", "mcp_tools", "system_tools", "skills", "system"}


class _FakeUsageClient:
    async def generate(self, request, **kwargs) -> Response:
        usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        return Response(success=True, content="回复", model="glm-4.7", usage=usage)


class _RecordingEventBus:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, Any, str]] = []

    async def emit(self, event_name: str, data: Any, source: str = "unknown") -> None:
        self.emitted.append((event_name, data, source))


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
    monkeypatch.setattr(manager._llm_repo, "insert_llm_usage", _boom)
    assert (await manager.generate("你好", profile="planner")).success


@pytest.mark.parametrize("with_bus", [True, False])
async def test_breakdown_goes_to_diagnostics_and_context_event(
    store: SQLiteDatabase, tmp_path: Path, with_bus: bool
) -> None:
    """breakdown 不再落库（列为 NULL），正文与分段占用走诊断小时流与 latest 快照；有总线才发事件。"""
    bus = _RecordingEventBus() if with_bus else None
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)
    manager = make_llm_manager(_FakeUsageClient(), llm_repo=store.llm, event_bus=bus, diagnostics_writer=writer)
    result = await manager.generate("你好", profile="planner")
    assert result.success
    writer.stop()

    assert [r["breakdown_json"] for r in await store.execute("SELECT breakdown_json FROM llm_requests")] == [None]
    (stream,) = list((tmp_path / "llm_requests").rglob("*.jsonl"))
    record = json.loads(stream.read_text(encoding="utf-8").strip())
    breakdown = record["breakdown"]
    assert record["request_id"] == breakdown["request_id"] == result.request_id
    assert (breakdown["api_prompt_tokens"], breakdown["calibrated"]) == (10, True)
    assert {section["key"] for section in breakdown["sections"]} == _SECTION_KEYS
    assert isinstance(record["timestamp_ms"], int)
    assert read_latest_breakdowns(base_dir=tmp_path)["glm-4.7"]["request_id"] == result.request_id

    if bus is None:
        return
    ((event_name, payload, source),) = bus.emitted
    assert (event_name, source) == (CoreEvents.LLM_CONTEXT_USED, "LLMManager")
    assert isinstance(payload, LLMContextUsedPayload)
    assert (payload.request_id, payload.model_name, payload.api_prompt_tokens) == (result.request_id, "glm-4.7", 10)
    assert {section.key for section in payload.sections} == _SECTION_KEYS


async def test_latest_breakdowns_query_returns_latest_per_model(store: SQLiteDatabase) -> None:
    """每模型最新一条带解剖行；无解剖或空模型名的行不进入结果。"""
    for request_id, model, ts, with_breakdown in (
        ("r_a1", "model-a", 1_000, True),
        ("r_a2", "model-a", 2_000, True),
        ("r_a3", "model-a", 3_000, False),
        ("r_b1", "model-b", 1_500, True),
        ("r_c1", "", 2_500, True),
    ):
        breakdown = {
            "request_id": request_id,
            "profile_name": "planner",
            "model_name": model,
            "api_prompt_tokens": 100,
            "local_total_tokens": 90,
            "calibrated": True,
            "sections": [{"key": "system", "tokens": 10, "raw_tokens": 9, "count": 1, "items": []}],
        }
        await store.llm.insert_llm_request(
            request_id=request_id,
            timestamp_ms=ts,
            profile_name="planner",
            model_name=model,
            prompt_tokens=100,
            breakdown_json=json.dumps(breakdown) if with_breakdown else None,
        )

    result = await store.llm.llm_requests_latest_breakdowns()
    assert {model: row["request_id"] for model, row in result.items()} == {"model-a": "r_a2", "model-b": "r_b1"}
