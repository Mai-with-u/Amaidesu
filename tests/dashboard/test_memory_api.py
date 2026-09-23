"""Memory API 测试：记忆管理端点（列表 / 增改 / 删除 / 召回测试 / 统计）。

使用真实 SQLiteDatabase + SimpleMemory（临时库）经 DashboardServer 挂载，
走完整 HTTP 层；造数直连 ``memory.ingest``（与 Agent 写入同链路），
不绕过被测的私有表契约。
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
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="memory-api-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


_server_ref_cache: dict = {}


def _run(coro) -> None:
    """同步用例内驱动异步调用（与既有 dashboard API 测试同模式）。"""
    loop = asyncio.new_event_loop()
    loop.run_until_complete(coro)
    loop.close()


@pytest.fixture
def client(temp_db_path: Path) -> Generator[TestClient, None, None]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    async def _build():
        store = SQLiteDatabase(temp_db_path)
        await store.initialize()
        memory = SimpleMemory(store)
        await memory.initialize()
        bus = EventBus()
        server = DashboardServer(
            event_bus=bus,
            config_service=None,  # type: ignore[arg-type]
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60235),
            memory=memory,
        )
        return store, memory, bus, server

    loop = asyncio.new_event_loop()
    store, memory, bus, server = loop.run_until_complete(_build())
    loop.close()

    _server_ref_cache["server"] = server
    _server_ref_cache["store"] = store
    _server_ref_cache["memory"] = memory
    set_dashboard_server(server)
    app = create_app()
    with TestClient(app) as c:
        yield c

    set_dashboard_server(None)

    _run(bus.cleanup())
    _run(store.close())


def _seed(count: int) -> None:
    """直连 memory.ingest 造数（重要度区分排序）。"""

    async def _seed_async():
        memory: SimpleMemory = _server_ref_cache["memory"]
        for i in range(count):
            await memory.ingest(
                f"事实条目{i} 关键词{i}",
                source="seed" if i % 2 == 0 else "webui",
                importance=i,
                tags=[f"tag{i}"],
            )

    _run(_seed_async())


# ===== 列表：字段 / 搜索 / 排序 / 分页 =====


def test_facts_empty(client: TestClient) -> None:
    body = client.get("/api/v1/memory/facts").json()
    assert body["total"] == 0
    assert body["items"] == []


def test_facts_item_shape(client: TestClient) -> None:
    _seed(2)
    body = client.get("/api/v1/memory/facts").json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert set(body["items"][0]) == {
        "id",
        "text",
        "source",
        "tags",
        "importance",
        "timestamp_ms",
    }
    # tags 已拆为列表
    assert isinstance(body["items"][0]["tags"], list)


def test_facts_search(client: TestClient) -> None:
    _seed(4)
    body = client.get("/api/v1/memory/facts", params={"search": "关键词1"}).json()
    assert body["total"] == 1
    assert "条目1" in body["items"][0]["text"]

    body = client.get("/api/v1/memory/facts", params={"search": "seed"}).json()
    assert body["total"] == 2  # 来源列命中


def test_facts_order_and_pagination(client: TestClient) -> None:
    _seed(5)
    body = client.get("/api/v1/memory/facts", params={"order_by": "importance", "limit": 2, "offset": 0}).json()
    assert body["total"] == 5
    assert [item["importance"] for item in body["items"]] == [4, 3]

    body = client.get("/api/v1/memory/facts", params={"order_by": "importance", "limit": 2, "offset": 2}).json()
    assert [item["importance"] for item in body["items"]] == [2, 1]


# ===== 新增 / 更新 / 删除 =====


def test_create_fact_uses_webui_source(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/memory/facts",
        json={"text": "手工录入的事实", "tags": ["手工", "测试"], "importance": 2},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert body["memory_id"] > 0

    listed = client.get("/api/v1/memory/facts", params={"search": "手工录入"}).json()
    item = listed["items"][0]
    assert item["source"] == "webui"
    assert item["tags"] == ["手工", "测试"]
    assert item["importance"] == 2


def test_create_fact_blank_text_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/memory/facts", json={"text": "   "})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is False
    assert body["memory_id"] == -1


def test_create_fact_missing_text_422(client: TestClient) -> None:
    resp = client.post("/api/v1/memory/facts", json={"tags": ["x"]})
    assert resp.status_code == 422


def test_update_fact_partial(client: TestClient) -> None:
    _seed(1)
    fact_id = client.get("/api/v1/memory/facts").json()["items"][0]["id"]

    resp = client.patch(f"/api/v1/memory/facts/{fact_id}", json={"importance": 9})
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    item = client.get("/api/v1/memory/facts").json()["items"][0]
    assert item["importance"] == 9
    assert item["text"] == "事实条目0 关键词0"  # 未提及字段不变


def test_update_fact_empty_body_422(client: TestClient) -> None:
    _seed(1)
    fact_id = client.get("/api/v1/memory/facts").json()["items"][0]["id"]
    resp = client.patch(f"/api/v1/memory/facts/{fact_id}", json={})
    assert resp.status_code == 422


def test_update_fact_missing_404(client: TestClient) -> None:
    resp = client.patch("/api/v1/memory/facts/424242", json={"importance": 1})
    assert resp.status_code == 404


def test_update_fact_clears_tags_with_empty_list(client: TestClient) -> None:
    _seed(1)
    fact_id = client.get("/api/v1/memory/facts").json()["items"][0]["id"]
    assert client.patch(f"/api/v1/memory/facts/{fact_id}", json={"tags": []}).status_code == 200
    item = client.get("/api/v1/memory/facts").json()["items"][0]
    assert item["tags"] == []


def test_delete_fact_and_missing_404(client: TestClient) -> None:
    _seed(1)
    fact_id = client.get("/api/v1/memory/facts").json()["items"][0]["id"]

    resp = client.delete(f"/api/v1/memory/facts/{fact_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert client.get("/api/v1/memory/facts").json()["total"] == 0

    resp = client.delete(f"/api/v1/memory/facts/{fact_id}")
    assert resp.status_code == 404


# ===== 召回测试 / 统计 =====


def test_recall_uses_same_path_as_agent(client: TestClient) -> None:
    _seed(3)
    resp = client.post("/api/v1/memory/recall", json={"query": "关键词1", "top_k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "关键词1"
    assert len(body["hits"]) >= 1
    assert set(body["hits"][0]) == {
        "memory_id",
        "text",
        "score",
        "timestamp_ms",
        "source",
        "tags",
    }


def test_recall_no_match_empty_hits(client: TestClient) -> None:
    _seed(2)
    body = client.post("/api/v1/memory/recall", json={"query": "毫不相关的内容"}).json()
    assert body["hits"] == []


def test_stats(client: TestClient) -> None:
    body = client.get("/api/v1/memory/stats").json()
    assert body == {"total_facts": 0, "sources": [], "latest_ms": 0}

    _seed(4)
    body = client.get("/api/v1/memory/stats").json()
    assert body["total_facts"] == 4
    assert body["latest_ms"] > 0
    assert {s["source"] for s in body["sources"]} == {"seed", "webui"}


# ===== 未注入记忆栈降级 =====


def test_memory_unavailable_503(client: TestClient) -> None:
    """server 未注入 memory 时端点 503（极简装配不炸其余 API）。"""
    from src.modules.dashboard.dependencies import set_dashboard_server

    bare = _server_ref_cache["server"]
    assert bare.memory is not None
    try:
        bare.memory = None
        set_dashboard_server(bare)
        resp = client.get("/api/v1/memory/facts")
        assert resp.status_code == 503
        resp = client.get("/api/v1/memory/stats")
        assert resp.status_code == 503
        resp = client.post("/api/v1/memory/recall", json={"query": "x"})
        assert resp.status_code == 503
    finally:
        bare.memory = _server_ref_cache["memory"]
        set_dashboard_server(bare)
