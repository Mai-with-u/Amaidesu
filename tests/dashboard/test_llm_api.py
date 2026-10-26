"""LLM API：用量聚合、趋势、请求历史与上下文水位，数据源为真实临时库（直连 insert_llm_call 造数）"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict

import pytest

import src.modules.dashboard.api.llm as llm_api
import src.modules.dashboard.services.llm_stats as llm_stats
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.llm.quota import ProviderQuota, QuotaWindow
from src.modules.llm.request_history_manager import RequestHistoryManager
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos.llm import LLMRequestInsert, LLMUsageInsert

MODEL_A = "test-model-a"
MODEL_B = "test-model-b"


class _FakeLLMManager:
    def __init__(self, windows: Dict[str, int]) -> None:
        self._windows = windows

    def get_model_context_windows(self) -> Dict[str, int]:
        return dict(self._windows)


@pytest.fixture(autouse=True)
def _isolate_context_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """/context-breakdown 优先读本机水位快照文件；本文件只走临时库，按"快照缺失"处理。"""
    monkeypatch.setattr(llm_api, "read_latest_breakdowns", lambda *args, **kwargs: {})


@pytest.fixture
async def llm_client(api, store: SQLiteDatabase, event_bus):
    """llm_client(windows=None)：/history* 经注入的 history_manager 读库，与组合根同款注入。"""

    async def _make(windows: Dict[str, int] | None = None):
        extra: Dict[str, Any] = {"request_history_manager": RequestHistoryManager(llm_repo=store.llm)}
        if windows:
            extra["llm_manager"] = _FakeLLMManager(windows)
        return await api(event_bus=event_bus, llm_repo=store.llm, **extra)

    yield _make


async def _seed_call(
    store: SQLiteDatabase,
    request_id: str,
    model_name: str,
    *,
    cache_hit: int = 0,
    cache_miss: int = 0,
    prompt: int = 100,
    cost: float = 0.01,
    timestamp_ms: int = 1_700_000_000_000,
    profile_name: str = "streamer",
    **request_extra: Any,
) -> None:
    """一次带缓存用量的调用：聚合账 + 请求明细同事务写入。"""
    tokens = {"prompt_tokens": prompt, "completion_tokens": 50, "total_tokens": prompt + 50}
    cache = {"cache_hit_tokens": cache_hit, "cache_miss_tokens": cache_miss}
    await store.llm.insert_llm_call(
        usage=LLMUsageInsert(
            model_name=model_name,
            provider_name="test",
            request_type="chat",
            cost=cost,
            request_id=request_id,
            timestamp_ms=timestamp_ms,
            **tokens,
            **cache,
        ),
        request=LLMRequestInsert(
            request_id=request_id,
            timestamp_ms=timestamp_ms,
            profile_name=profile_name,
            model_name=model_name,
            cost=cost,
            latency_ms=120,
            **tokens,
            **cache,
            **request_extra,
        ),
    )


def _breakdown_payload(request_id: str, model_name: str, prompt: int, profile_name: str = "replyer") -> Dict[str, Any]:
    """一份上下文分段占用解剖（诊断文件数据形态）。"""
    return {
        "request_id": request_id,
        "profile_name": profile_name,
        "model_name": model_name,
        "timestamp_ms": 1_700_000_000_000,
        "api_prompt_tokens": prompt,
        "local_total_tokens": 90,
        "calibrated": True,
        "sections": [
            {"key": "system", "tokens": 10, "raw_tokens": 9, "count": 1, "items": [{"name": "system", "tokens": 10}]},
            {"key": "messages", "tokens": 60, "raw_tokens": 54, "count": 2, "items": []},
            {"key": "tools", "tokens": 30, "raw_tokens": 27, "count": 1, "items": [{"name": "reply", "tokens": 30}]},
        ],
    }


async def _seed_breakdown(
    store: SQLiteDatabase, request_id: str, model_name: str, timestamp_ms: int, prompt: int = 100, **kwargs: Any
) -> None:
    """带上下文分段解剖的请求明细（库行只存统计；分段占用经诊断文件回查）。"""
    profile_name = kwargs.get("profile_name", "replyer")
    await store.llm.insert_llm_request(
        request_id=request_id,
        timestamp_ms=timestamp_ms,
        profile_name=profile_name,
        model_name=model_name,
        prompt_tokens=prompt,
    )


async def test_usage_and_summary_aggregate_cache(llm_client, store) -> None:
    client = await llm_client()
    # 冷启动：空库返回 200 空数据，不是 500
    assert (await client.get("/api/v1/llm/usage")).json() == {}
    empty = (await client.get("/api/v1/llm/usage/summary")).json()
    assert (empty["total_calls"], empty["total_tokens"], empty["model_count"]) == (0, 0, 0)

    await _seed_call(store, "r1", MODEL_A, cache_hit=300, cache_miss=700, cost=0.02)
    await _seed_call(store, "r2", MODEL_A, cache_hit=200, cache_miss=800)
    await _seed_call(store, "r3", MODEL_B, cache_miss=500)

    body = (await client.get("/api/v1/llm/usage")).json()
    model_a = body[MODEL_A]
    assert (model_a["cache_hit_tokens"], model_a["cache_miss_tokens"], model_a["total_calls"]) == (500, 1500, 2)
    assert model_a["cache_hit_rate"] == pytest.approx(0.25) and model_a["total_prompt_tokens"] == 200
    assert model_a["first_call_time"] is not None and model_a["last_call_time"] is not None
    assert (body[MODEL_B]["cache_hit_tokens"], body[MODEL_B]["cache_hit_rate"]) == (0, 0.0)

    summary = (await client.get("/api/v1/llm/usage/summary")).json()
    assert (summary["cache_hit_tokens"], summary["cache_miss_tokens"]) == (500, 2000)
    assert summary["cache_hit_rate"] == pytest.approx(500 / 2500)
    assert (summary["total_calls"], summary["model_count"]) == (3, 2)
    assert summary["total_cost"] == pytest.approx(0.04)


async def test_statistics_aggregate_cache_with_time_window(llm_client, store) -> None:
    client = await llm_client()
    await _seed_call(store, "s1", MODEL_A, cache_hit=300, cache_miss=700)
    await _seed_call(
        store, "s2", MODEL_B, cache_hit=100, cache_miss=400, profile_name="game", timestamp_ms=1_700_000_005_000
    )

    stats = (await client.get("/api/v1/llm/history/statistics")).json()
    assert (stats["cache_hit_tokens"], stats["cache_miss_tokens"]) == (400, 1100)
    assert stats["cache_hit_rate"] == pytest.approx(400 / 1500)
    assert stats["model_stats"][MODEL_A]["cache_hit_tokens"] == 300
    assert stats["model_stats"][MODEL_B]["cache_hit_rate"] == pytest.approx(0.2)

    windowed = (await client.get("/api/v1/llm/history/statistics", params={"start_time": 1_700_000_000_001})).json()
    assert (windowed["cache_hit_tokens"], windowed["cache_miss_tokens"]) == (100, 400)


async def test_usage_trends_daily_aggregation(llm_client, store) -> None:
    """逐日聚合、缺失日期补零（命中率 None：未上报 ≠ 零命中），按模型细分只列有数据的日子。"""
    client = await llm_client()
    empty = (await client.get("/api/v1/llm/usage/trends", params={"days": 3})).json()
    assert len(empty["points"]) == 3 and empty["model_points"] == []
    assert all(p["total_calls"] == 0 and p["cache_hit_rate"] is None for p in empty["points"])

    today_ms = int(datetime.now().timestamp() * 1000)
    yesterday_ms = today_ms - 86_400_000
    await _seed_call(store, "t1", MODEL_A, cache_hit=300, cache_miss=200, cost=0.03, timestamp_ms=yesterday_ms)
    await _seed_call(store, "t2", MODEL_A, cache_hit=100, cost=0.01, timestamp_ms=yesterday_ms + 60_000)
    await _seed_call(store, "t3", MODEL_B, cache_miss=400, cost=0.02, timestamp_ms=today_ms)

    body = (await client.get("/api/v1/llm/usage/trends", params={"days": 7})).json()
    assert body["days"] == 7 and len(body["points"]) == 7
    points = {p["date"]: p for p in body["points"]}
    today = datetime.now().date()
    today_str, yesterday_str = today.isoformat(), (today - timedelta(days=1)).isoformat()

    t, y = points[today_str], points[yesterday_str]
    assert (t["total_calls"], t["cache_hit_tokens"], t["cache_miss_tokens"], t["cache_hit_rate"]) == (1, 0, 400, 0.0)
    assert t["cost"] == pytest.approx(0.02)
    assert (y["total_calls"], y["cache_hit_tokens"], y["cache_miss_tokens"]) == (2, 400, 200)
    assert y["cache_hit_rate"] == pytest.approx(400 / 600)
    gap = points[(today - timedelta(days=3)).isoformat()]
    assert (gap["total_calls"], gap["total_tokens"], gap["cache_hit_rate"]) == (0, 0, None)

    model_points = body["model_points"]
    assert {(p["date"], p["model_name"]) for p in model_points} == {(yesterday_str, MODEL_A), (today_str, MODEL_B)}
    model_a = next(p for p in model_points if p["model_name"] == MODEL_A)
    assert model_a["total_calls"] == 2 and model_a["cache_hit_rate"] == pytest.approx(400 / 600)

    for days in (0, 366):
        assert (await client.get("/api/v1/llm/usage/trends", params={"days": days})).status_code == 422


async def test_history_rows_carry_tool_call_preview_and_usage_details(
    llm_client, store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """列表行只带统计与摘要；详情正文经诊断文件回查合并（tool_calls / 原始 usage 随详情透出）。"""
    client = await llm_client()
    raw_usage = json.dumps({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
    tool_calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "reply", "arguments": {"speech": "今天开的就是《我的世界》"}},
        }
    ]
    await _seed_call(
        store,
        "tc1",
        MODEL_A,
        cache_hit=3840,
        cache_miss=84,
        profile_name="planner",
        reasoning_tokens=42,
        image_count=2,
    )
    payload = {"request_params": {}, "tool_calls": tool_calls, "reasoning_content": None, "usage_raw_json": raw_usage}
    monkeypatch.setattr(
        llm_stats, "read_llm_request_payload", lambda request_id, ts: payload if request_id == "tc1" else None
    )

    body = (await client.get("/api/v1/llm/history", params={"page": 1, "page_size": 10})).json()
    row = next(item for item in body["items"] if item["request_id"] == "tc1")
    # 0/0 = 未上报 ≠ 零命中，前端据此算命中率；列表正名为 profile_name
    assert (row["cache_hit_tokens"], row["cache_miss_tokens"], row["profile_name"]) == (3840, 84, "planner")
    assert row["reasoning_tokens"] == 42
    assert row["usage_raw_json"] is None  # 正文字段不再随列表行透出，详情接口回查诊断文件
    detail = (await client.get("/api/v1/llm/history/tc1")).json()
    assert detail["usage_raw_json"] == raw_usage
    assert detail["tool_calls"] == tool_calls
    # 注入截图计数随列表行透出（WebUI 徽标数据源）；详情响应不携带
    assert row["image_count"] == 2
    assert "image_count" not in detail


async def test_history_models_are_distinct_and_sorted(llm_client, store) -> None:
    client = await llm_client()
    for request_id, model in (("m1", "z-model"), ("m2", "a-model"), ("m3", "a-model")):
        await _seed_call(store, request_id, model)
    assert (await client.get("/api/v1/llm/history/models")).json() == ["a-model", "z-model"]


@pytest.mark.parametrize("windows", [None, {MODEL_A: 128_000, MODEL_B: 0}], ids=["no_llm_manager", "with_windows"])
async def test_usage_context_window_and_latest_prompt(llm_client, store, windows) -> None:
    """窗口来自引擎装配索引（未注入或未配置为 0，前端据此隐藏水位）；水位分子是最新一条 prompt，不是累加。"""
    client = await llm_client(windows)
    for i, prompt in enumerate((100, 400, 800)):
        await _seed_call(store, f"lp-{i}", MODEL_A, prompt=prompt, timestamp_ms=1_700_000_000_000 + i * 10_000)
    await _seed_call(store, "lp-b", MODEL_B, prompt=200)

    body = (await client.get("/api/v1/llm/usage")).json()
    assert (body[MODEL_A]["total_prompt_tokens"], body[MODEL_A]["last_call_prompt_tokens"]) == (1300, 800)
    assert body[MODEL_B]["last_call_prompt_tokens"] == 200
    expected = windows or {MODEL_A: 0, MODEL_B: 0}
    assert {m: body[m]["context_window"] for m in (MODEL_A, MODEL_B)} == expected


async def test_context_breakdown_latest_per_model(llm_client, store, monkeypatch: pytest.MonkeyPatch) -> None:
    client = await llm_client()
    assert (await client.get("/api/v1/llm/context-breakdown")).json() == {}

    # 分段占用只存在于诊断快照文件（v17 起 breakdown 库列已退役）；快照每模型一份最新水位
    snapshots = {
        MODEL_A: _breakdown_payload("cb-a2", MODEL_A, 500),
        MODEL_B: _breakdown_payload("cb-b1", MODEL_B, 100, profile_name="planner"),
    }
    monkeypatch.setattr(llm_api, "read_latest_breakdowns", lambda *args, **kwargs: snapshots)
    await _seed_breakdown(store, "cb-a2", MODEL_A, 2_000, prompt=500)
    await _seed_breakdown(store, "cb-b1", MODEL_B, 1_500, profile_name="planner")

    body = (await client.get("/api/v1/llm/context-breakdown")).json()
    model_a = body[MODEL_A]
    assert (model_a["request_id"], model_a["api_prompt_tokens"], model_a["calibrated"]) == ("cb-a2", 500, True)
    assert {section["key"] for section in model_a["sections"]} == {"system", "messages", "tools"}
    assert (model_a["context_window"], model_a["free_tokens"]) == (0, None)
    assert body[MODEL_B]["profile_name"] == "planner"


async def test_context_breakdown_by_request(llm_client, store, monkeypatch: pytest.MonkeyPatch) -> None:
    """按请求 ID 取分段：诊断文件命中返回明细，未命中或行不存在返回 null，前端按空态渲染。"""
    client = await llm_client()
    await _seed_breakdown(store, "cbr-1", MODEL_A, 1_000, prompt=2_933)
    await _seed_breakdown(store, "cbr-2", MODEL_A, 2_000)

    def _read_from_file(request_id: str, timestamp_ms: int):
        if request_id == "cbr-1":
            return _breakdown_payload("cbr-1", MODEL_A, 2_933)
        return None

    monkeypatch.setattr(llm_api, "read_request_breakdown", _read_from_file)

    body = (await client.get("/api/v1/llm/context-breakdown/cbr-1")).json()
    assert (body["request_id"], body["api_prompt_tokens"]) == ("cbr-1", 2_933)
    assert {section["key"] for section in body["sections"]} == {"system", "messages", "tools"}
    for missing in ("cbr-2", "nonexistent"):
        assert (await client.get(f"/api/v1/llm/context-breakdown/{missing}")).json() is None


async def test_context_breakdown_threads_window_from_llm_manager(
    llm_client, store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """free = max(0, window - used)；窗口未配置时 free 为 None。"""
    client = await llm_client({MODEL_A: 128_000, MODEL_B: 0})
    await _seed_breakdown(store, "cbw-a1", MODEL_A, 1_000, prompt=10_000)
    await _seed_breakdown(store, "cbw-b1", MODEL_B, 1_100, prompt=300)
    snapshots = {
        MODEL_A: _breakdown_payload("cbw-a1", MODEL_A, 10_000),
        MODEL_B: _breakdown_payload("cbw-b1", MODEL_B, 300),
    }
    monkeypatch.setattr(llm_api, "read_latest_breakdowns", lambda *args, **kwargs: snapshots)
    monkeypatch.setattr(
        llm_api, "read_request_breakdown", lambda request_id, ts: snapshots[MODEL_A] if request_id == "cbw-a1" else None
    )

    body = (await client.get("/api/v1/llm/context-breakdown")).json()
    assert (body[MODEL_A]["context_window"], body[MODEL_A]["free_tokens"]) == (128_000, 118_000)
    assert (body[MODEL_B]["context_window"], body[MODEL_B]["free_tokens"]) == (0, None)
    single = (await client.get("/api/v1/llm/context-breakdown/cbw-a1")).json()
    assert (single["context_window"], single["free_tokens"]) == (128_000, 118_000)


async def test_quota_is_null_without_llm_manager_and_forwards_slashed_model_identifiers(llm_client) -> None:
    """上下文面板 Usage 区数据源：未注入 LLMManager 时返回 null（前端隐藏 Usage 区）而非报错；
    模型标识走查询参数，OpenRouter 式带斜杠的标识原样到达额度服务。"""
    client = await llm_client()
    resp = await client.get("/api/v1/llm/quota", params={"model_name": MODEL_A})
    assert resp.status_code == 200 and resp.json() is None

    received: list[str] = []

    class _StubQuotaService:
        async def get_quota_for_model(self, model_identifier: str) -> ProviderQuota:
            received.append(model_identifier)
            return ProviderQuota(
                provider_name="or",
                platform="openrouter",
                platform_label="OpenRouter",
                plan_label="Pay-as-you-go",
                windows=[QuotaWindow(kind="balance", label="Credits", amount=12.5, currency="USD")],
                fetched_at_ms=1_700_000_000_000,
            )

    get_dashboard_server().provider_quota = _StubQuotaService()
    body = (await client.get("/api/v1/llm/quota", params={"model_name": "anthropic/claude-sonnet"})).json()
    assert received == ["anthropic/claude-sonnet"]
    assert (body["platform"], body["windows"][0]["amount"]) == ("openrouter", 12.5)
