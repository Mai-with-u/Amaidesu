"""
llm_usage 落库链路单测

覆盖：
- insert_llm_usage 往返：字段完整落列、可选字段默认、毫秒时间戳
- LLMManager 成功调用后旁路写 llm_usage（注入 store 时 +1 行；未注入不落库）
- 落库失败只降级记日志，不阻断 LLM 调用链
- 成功调用同步落上下文分段解剖（breakdown_json）并发布 llm.context.used 事件
- ``llm_requests_latest_breakdowns``：每模型最新带解剖行，无解剖模型不返回
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

from src.modules.events.names import CoreEvents
from src.modules.events.payloads import LLMContextUsedPayload
from src.modules.llm.bootstrap import _ResolvedModel, _ResolvedProfile
from src.modules.llm.engine import LLMManager
from src.modules.llm.payload import Response as PayloadResponse
from src.modules.llm.payload import Usage as PayloadUsage
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="storage-llm-usage-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def store(temp_db_path: Path):
    """直接使用 SQLiteDatabase：断言用裸 SQL，写入用 db.llm 仓储。"""
    db = SQLiteDatabase(temp_db_path)
    await db.initialize()
    yield db
    await db.close()


# ===== insert_llm_usage =====


@pytest.mark.asyncio
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
    assert rowid > 0
    rows = await store.execute("SELECT * FROM llm_usage WHERE id=?", (rowid,))
    assert len(rows) == 1
    row = rows[0]
    assert row["model_name"] == "glm-4.7"
    assert row["provider_name"] == "zhipu"
    assert row["profile_name"] == "llm"
    assert row["request_type"] == "chat"
    assert row["prompt_tokens"] == 100
    assert row["total_tokens"] == 150
    assert row["cache_hit_tokens"] == 80
    assert row["cache_miss_tokens"] == 20
    assert row["cost"] == pytest.approx(0.0123)
    assert row["duration_ms"] == 850
    assert row["timestamp_ms"] == 1_700_000_000_000
    # 可选字段默认 NULL；毫秒命名硬规则
    assert row["assign_name"] is None
    assert row["live_session_id"] is None


# ===== LLMManager 旁路落库 =====


class _FakeUsageClient:
    """伪客户端：generate 成功并返回 usage（不发起网络请求）。"""

    async def generate(self, request, **kwargs):
        return PayloadResponse(
            success=True,
            content="回复",
            model="glm-4.7",
            usage=PayloadUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


class _RecordingEventBus:
    """伪事件总线：只记录 emit 调用（事件名, payload, source）。"""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, Any, str]] = []

    async def emit(self, event_name: str, data: Any, source: str = "unknown") -> None:
        self.emitted.append((event_name, data, source))


def _make_manager_with_fake_client(
    store: SQLiteDatabase | None,
    event_bus: Any | None = None,
) -> LLMManager:
    manager = LLMManager(llm_repo=store.llm if store is not None else None, event_bus=event_bus)
    # 直接注入客户端与配置，绕过 setup() 的真实 provider 装配
    fake_client = _FakeUsageClient()
    manager._provider_clients["zhipu"] = fake_client
    manager._providers["zhipu"] = ({"name": "zhipu", "client_type": "openai"}, fake_client)
    manager._models["glm-4.7"] = (
        {"name": "glm-4.7", "model_identifier": "glm-4.7", "api_provider": "zhipu"},
        "zhipu",
    )
    manager._profiles["planner"] = _ResolvedProfile(
        profile_name="planner",
        slow_threshold_ms=15_000,
        selection_strategy="sequential",
        seed=0,
        temperature=0.3,
        models=[_ResolvedModel(model_name="glm-4.7", model_identifier="glm-4.7", provider_name="zhipu")],
    )
    manager._model_call_counts["planner"] = {}
    return manager


@pytest.mark.asyncio
async def test_successful_call_persists_llm_usage(store: SQLiteDatabase, monkeypatch) -> None:
    manager = _make_manager_with_fake_client(store)
    result = await manager.generate("你好", profile="planner")
    assert result.success

    rows = await store.execute("SELECT * FROM llm_usage")
    assert len(rows) == 1
    row = rows[0]
    assert row["model_name"] == "glm-4.7"
    assert row["provider_name"] == "zhipu"
    assert row["profile_name"] == "planner"
    assert row["prompt_tokens"] == 10
    assert row["total_tokens"] == 15
    assert row["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_call_without_store_does_not_persist(monkeypatch) -> None:
    manager = _make_manager_with_fake_client(None)  # 未注入 store：不落库也不报错
    result = await manager.generate("你好", profile="planner")
    assert result.success


@pytest.mark.asyncio
async def test_persist_failure_degrades_without_breaking_call(store: SQLiteDatabase, monkeypatch) -> None:
    manager = _make_manager_with_fake_client(store)

    async def _boom(**kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(manager._llm_repo, "insert_llm_usage", _boom)
    result = await manager.generate("你好", profile="planner")
    # 落库失败不阻断调用链，调用仍成功返回
    assert result.success


# ===== 上下文分段解剖落库 + llm.context.used 事件 =====


@pytest.mark.asyncio
async def test_successful_call_persists_breakdown_and_emits_event(store: SQLiteDatabase) -> None:
    """成功调用：breakdown_json 随请求明细落库 + llm.context.used 事件各一条。"""
    bus = _RecordingEventBus()
    manager = _make_manager_with_fake_client(store, event_bus=bus)
    result = await manager.generate("你好", profile="planner")
    assert result.success

    rows = await store.execute("SELECT breakdown_json FROM llm_requests")
    assert len(rows) == 1
    breakdown = json.loads(rows[0]["breakdown_json"])
    assert breakdown["request_id"] == result.request_id
    assert breakdown["api_prompt_tokens"] == 10
    assert breakdown["calibrated"] is True
    assert {section["key"] for section in breakdown["sections"]} == {
        "messages",
        "mcp_tools",
        "system_tools",
        "skills",
        "system",
    }

    assert len(bus.emitted) == 1
    event_name, payload, source = bus.emitted[0]
    assert event_name == CoreEvents.LLM_CONTEXT_USED
    assert source == "LLMManager"
    assert isinstance(payload, LLMContextUsedPayload)
    assert payload.request_id == result.request_id
    assert payload.model_name == "glm-4.7"
    assert payload.api_prompt_tokens == 10
    assert {section.key for section in payload.sections} == {
        "messages",
        "mcp_tools",
        "system_tools",
        "skills",
        "system",
    }


@pytest.mark.asyncio
async def test_call_without_event_bus_does_not_emit(store: SQLiteDatabase) -> None:
    """未注入事件总线：事件通道静默跳过，落库与调用链不受影响。"""
    manager = _make_manager_with_fake_client(store, event_bus=None)
    result = await manager.generate("你好", profile="planner")
    assert result.success
    rows = await store.execute("SELECT breakdown_json FROM llm_requests")
    assert json.loads(rows[0]["breakdown_json"])["api_prompt_tokens"] == 10


@pytest.mark.asyncio
async def test_latest_breakdowns_query_returns_latest_per_model(store: SQLiteDatabase) -> None:
    """每模型最新一条带解剖行；无解剖/空模型名行不进入结果。"""

    async def _seed(request_id: str, model: str, ts: int, with_breakdown: bool) -> None:
        breakdown = (
            json.dumps(
                {
                    "request_id": request_id,
                    "profile_name": "planner",
                    "model_name": model,
                    "api_prompt_tokens": 100,
                    "local_total_tokens": 90,
                    "calibrated": True,
                    "sections": [
                        {"key": "system", "tokens": 10, "raw_tokens": 9, "count": 1, "items": []},
                        {"key": "messages", "tokens": 60, "raw_tokens": 54, "count": 2, "items": []},
                        {"key": "tools", "tokens": 30, "raw_tokens": 27, "count": 1, "items": []},
                    ],
                },
                ensure_ascii=False,
            )
            if with_breakdown
            else None
        )
        await store.llm.insert_llm_request(
            request_id=request_id,
            timestamp_ms=ts,
            profile_name="planner",
            model_name=model,
            prompt_tokens=100,
            breakdown_json=breakdown,
        )

    await _seed("r_a1", "model-a", 1_000, with_breakdown=True)
    await _seed("r_a2", "model-a", 2_000, with_breakdown=True)
    await _seed("r_a3", "model-a", 3_000, with_breakdown=False)
    await _seed("r_b1", "model-b", 1_500, with_breakdown=True)
    await _seed("r_c1", "", 2_500, with_breakdown=True)

    result = await store.llm.llm_requests_latest_breakdowns()
    assert set(result.keys()) == {"model-a", "model-b"}
    assert result["model-a"]["request_id"] == "r_a2"
    assert result["model-b"]["request_id"] == "r_b1"
