"""LLM API 测试：用量数据源切 SQLite 后的 /usage、/usage/summary、/history/statistics。

使用真实 SQLiteDatabase（临时库）经 DashboardServer 挂载，走完整 HTTP 层；
造数直连 ``store.llm.insert_llm_call``（同事务写聚合账与请求明细），
不经过 observation 业务写链。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from src.modules.events.event_bus import EventBus
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos.llm import LLMRequestInsert, LLMUsageInsert

MODEL_A = "test-model-a"
MODEL_B = "test-model-b"


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="llm-api-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


_server_ref_cache = {}


def _seed_call(
    store: SQLiteDatabase,
    *,
    request_id: str,
    model_name: str,
    cache_hit: int = 0,
    cache_miss: int = 0,
    prompt: int = 100,
    completion: int = 50,
    cost: float = 0.01,
    timestamp_ms: int = 1_700_000_000_000,
    client_type: str = "streamer",
) -> None:
    """直连 store 造一次带 cache 用量的 LLM 调用（聚合账 + 请求明细）。"""

    async def _seed():
        await store.llm.insert_llm_call(
            usage=LLMUsageInsert(
                model_name=model_name,
                provider_name="test",
                request_type="chat",
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                cache_hit_tokens=cache_hit,
                cache_miss_tokens=cache_miss,
                cost=cost,
                request_id=request_id,
                timestamp_ms=timestamp_ms,
            ),
            request=LLMRequestInsert(
                request_id=request_id,
                timestamp_ms=timestamp_ms,
                client_type=client_type,
                model_name=model_name,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                cache_hit_tokens=cache_hit,
                cache_miss_tokens=cache_miss,
                cost=cost,
                latency_ms=120,
            ),
        )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_seed())
    loop.close()


@pytest.fixture
def client(temp_db_path: Path) -> Generator[TestClient, None, None]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer
    from src.modules.llm.request_history_manager import get_global_request_history_manager

    async def _build():
        store = SQLiteDatabase(temp_db_path)
        await store.initialize()
        bus = EventBus()

        server = DashboardServer(
            event_bus=bus,
            config_service=None,  # type: ignore[arg-type]
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60216),
            llm_repo=store.llm,
        )
        return store, bus, server

    loop = asyncio.new_event_loop()
    store, bus, server = loop.run_until_complete(_build())
    loop.close()

    # /history* 端点经全局 history_manager 读库，组合根等价注入（main.py 同款）
    get_global_request_history_manager().attach_repo(store.llm)

    _server_ref_cache["server"] = server
    _server_ref_cache["store"] = store
    set_dashboard_server(server)
    app = create_app()
    with TestClient(app) as c:
        yield c

    set_dashboard_server(None)
    get_global_request_history_manager().attach_repo(None)

    async def _teardown():
        await bus.cleanup()
        await store.close()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_teardown())
    loop.close()


def test_usage_empty_db_returns_200(client: TestClient) -> None:
    """冷启动：库空时 /usage 与 /usage/summary 返回 200 空数据（非 500）。"""
    resp = client.get("/api/v1/llm/usage")
    assert resp.status_code == 200
    assert resp.json() == {}

    resp = client.get("/api/v1/llm/usage/summary")
    assert resp.status_code == 200
    assert resp.json()["total_calls"] == 0
    assert resp.json()["total_tokens"] == 0
    assert resp.json()["model_count"] == 0


def test_usage_and_summary_aggregate_cache(client: TestClient) -> None:
    """入库带 cache 的调用后，/usage 与 /usage/summary 聚合出非 0 cache 用量。"""
    store = _server_ref_cache["store"]
    _seed_call(store, request_id="r1", model_name=MODEL_A, cache_hit=300, cache_miss=700, cost=0.02)
    _seed_call(store, request_id="r2", model_name=MODEL_A, cache_hit=200, cache_miss=800)
    _seed_call(store, request_id="r3", model_name=MODEL_B, cache_hit=0, cache_miss=500)

    body = client.get("/api/v1/llm/usage").json()
    assert set(body.keys()) == {MODEL_A, MODEL_B}
    model_a = body[MODEL_A]
    assert model_a["cache_hit_tokens"] == 500
    assert model_a["cache_miss_tokens"] == 1500
    assert model_a["cache_hit_rate"] == pytest.approx(0.25)
    assert model_a["total_calls"] == 2
    assert model_a["total_prompt_tokens"] == 200
    assert model_a["first_call_time"] is not None
    assert model_a["last_call_time"] is not None
    assert body[MODEL_B]["cache_hit_tokens"] == 0
    assert body[MODEL_B]["cache_hit_rate"] == 0.0

    summary = client.get("/api/v1/llm/usage/summary").json()
    assert summary["cache_hit_tokens"] == 500
    assert summary["cache_miss_tokens"] == 2000
    assert summary["cache_hit_rate"] == pytest.approx(500 / 2500)
    assert summary["total_calls"] == 3
    assert summary["model_count"] == 2
    assert abs(summary["total_cost"] - 0.04) < 1e-9


def test_statistics_aggregate_cache(client: TestClient) -> None:
    """/history/statistics 返回 cache 聚合（总体与按模型），支持时间窗参数。"""
    store = _server_ref_cache["store"]
    _seed_call(store, request_id="s1", model_name=MODEL_A, cache_hit=300, cache_miss=700)
    _seed_call(
        store,
        request_id="s2",
        model_name=MODEL_B,
        cache_hit=100,
        cache_miss=400,
        client_type="game",
        timestamp_ms=1_700_000_005_000,
    )

    stats = client.get("/api/v1/llm/history/statistics").json()
    assert stats["cache_hit_tokens"] == 400
    assert stats["cache_miss_tokens"] == 1100
    assert stats["cache_hit_rate"] == pytest.approx(400 / 1500)
    assert stats["model_stats"][MODEL_A]["cache_hit_tokens"] == 300
    assert stats["model_stats"][MODEL_B]["cache_miss_tokens"] == 400
    assert stats["model_stats"][MODEL_B]["cache_hit_rate"] == pytest.approx(0.2)

    # 时间窗只含 s2 时 cache 聚合随之收窄
    windowed = client.get(
        "/api/v1/llm/history/statistics",
        params={"start_time": 1_700_000_000_001},
    ).json()
    assert windowed["cache_hit_tokens"] == 100
    assert windowed["cache_miss_tokens"] == 400


def test_usage_trends_daily_aggregation(client: TestClient) -> None:
    """/usage/trends 逐日聚合 + 缺失日期补零 + 命中率计算。"""
    from datetime import datetime, timedelta

    store = _server_ref_cache["store"]
    # 今天与昨天各造两次调用（含跨模型），命中率 400/(400+600)=0.4
    today_ms = int(datetime.now().timestamp() * 1000)
    yesterday_ms = today_ms - timedelta(days=1).total_seconds() * 1000
    _seed_call(
        store,
        request_id="t1",
        model_name=MODEL_A,
        cache_hit=300,
        cache_miss=200,
        cost=0.03,
        timestamp_ms=yesterday_ms,
    )
    _seed_call(
        store,
        request_id="t2",
        model_name=MODEL_A,
        cache_hit=100,
        cache_miss=0,
        cost=0.01,
        timestamp_ms=yesterday_ms + 60_000,
    )
    _seed_call(
        store,
        request_id="t3",
        model_name=MODEL_B,
        cache_hit=0,
        cache_miss=400,
        cost=0.02,
        timestamp_ms=today_ms,
    )

    body = client.get("/api/v1/llm/usage/trends", params={"days": 7}).json()
    assert body["days"] == 7
    assert len(body["points"]) == 7  # 补零后时间轴连续

    points = {p["date"]: p for p in body["points"]}
    today_str = datetime.now().date().isoformat()
    yesterday_str = (datetime.now().date() - timedelta(days=1)).isoformat()

    today = points[today_str]
    assert today["total_calls"] == 1
    assert today["cache_hit_tokens"] == 0
    assert today["cache_miss_tokens"] == 400
    assert today["cache_hit_rate"] == 0.0
    assert abs(today["cost"] - 0.02) < 1e-9

    yesterday = points[yesterday_str]
    assert yesterday["total_calls"] == 2
    assert yesterday["cache_hit_tokens"] == 400
    assert yesterday["cache_miss_tokens"] == 200
    assert yesterday["cache_hit_rate"] == pytest.approx(400 / 600)

    # 补零日期：无数据的日期全零且命中率 None（未上报 ≠ 零命中）
    empty = points[(datetime.now().date() - timedelta(days=3)).isoformat()]
    assert empty["total_calls"] == 0
    assert empty["total_tokens"] == 0
    assert empty["cache_hit_rate"] is None

    # 按模型细分只有有数据的日子
    model_points = body["model_points"]
    assert {(p["date"], p["model_name"]) for p in model_points} == {
        (yesterday_str, MODEL_A),
        (today_str, MODEL_B),
    }
    model_a_point = next(p for p in model_points if p["model_name"] == MODEL_A)
    assert model_a_point["total_calls"] == 2
    assert model_a_point["cache_hit_rate"] == pytest.approx(400 / 600)

    # days 参数越界被校验拒绝
    assert client.get("/api/v1/llm/usage/trends", params={"days": 0}).status_code == 422
    assert client.get("/api/v1/llm/usage/trends", params={"days": 366}).status_code == 422


def test_usage_trends_empty_db(client: TestClient) -> None:
    """冷启动：库空时 /usage/trends 返回补零时间轴（非 500）。"""
    body = client.get("/api/v1/llm/usage/trends", params={"days": 3}).json()
    assert body["days"] == 3
    assert len(body["points"]) == 3
    assert all(p["total_calls"] == 0 for p in body["points"])
    assert all(p["cache_hit_rate"] is None for p in body["points"])
    assert body["model_points"] == []
