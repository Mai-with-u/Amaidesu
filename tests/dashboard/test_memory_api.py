"""Memory API 测试：观众画像管理端点（画像列表 / 纠正 / 删除，事实查看 / 删除，统计）。

使用真实 SQLiteDatabase + SimpleMemory（临时库）经 DashboardServer 挂载，
走完整 HTTP 层；造数直连 ``SimpleMemory`` 写入方法（与 Agent 侧写库同链路）。
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


def _seed_profiles(count: int) -> None:
    """直连 SimpleMemory 造画像（user_id 递增，文本含可搜索关键词）。"""

    async def _seed_async():
        memory: SimpleMemory = _server_ref_cache["memory"]
        for i in range(count):
            await memory.upsert_viewer_profile(
                platform="bilibili",
                user_id=f"u_{i}",
                profile_text=f"画像{i}：喜欢Minecraft" if i % 2 else f"画像{i}：喜欢恐怖游戏",
                last_compressed_at_ms=1_000 + i,
            )

    _run(_seed_async())


def _seed_facts(platform: str = "bilibili", user_id: str = "u_0", count: int = 3) -> None:
    """直连 SimpleMemory 造事实。"""

    async def _seed_async():
        memory: SimpleMemory = _server_ref_cache["memory"]
        for i in range(count):
            await memory.add_viewer_fact(
                platform=platform,
                user_id=user_id,
                fact_text=f"事实条目{i} 关键词{i}",
                source_message_id=f"msg_{i}",
            )

    _run(_seed_async())


# ===== 画像列表：字段 / 搜索 / 分页 =====


def test_profiles_empty(client: TestClient) -> None:
    body = client.get("/api/v1/memory/profiles").json()
    assert body == {"total": 0, "items": []}


def test_profiles_item_shape(client: TestClient) -> None:
    _seed_profiles(1)
    body = client.get("/api/v1/memory/profiles").json()
    assert body["total"] == 1
    item = body["items"][0]
    assert set(item) == {"platform", "user_id", "profile_text", "last_compressed_at_ms", "updated_at_ms"}
    assert item["platform"] == "bilibili"
    assert item["user_id"] == "u_0"
    assert item["last_compressed_at_ms"] == 1_000


def test_profiles_search(client: TestClient) -> None:
    _seed_profiles(4)
    body = client.get("/api/v1/memory/profiles", params={"search": "Minecraft"}).json()
    assert body["total"] == 2  # 偶数号条目
    assert all("Minecraft" in item["profile_text"] for item in body["items"])


def test_profiles_pagination(client: TestClient) -> None:
    _seed_profiles(5)
    page1 = client.get("/api/v1/memory/profiles", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/v1/memory/profiles", params={"limit": 2, "offset": 2}).json()
    assert page1["total"] == 5 and len(page1["items"]) == 2
    assert page2["total"] == 5 and len(page2["items"]) == 2
    ids1 = {item["user_id"] for item in page1["items"]}
    ids2 = {item["user_id"] for item in page2["items"]}
    assert ids1.isdisjoint(ids2)


# ===== 画像纠正 / 删除 =====


def test_update_profile_text(client: TestClient) -> None:
    _seed_profiles(1)
    res = client.patch(
        "/api/v1/memory/profiles/bilibili/u_0",
        json={"profile_text": "人工纠正后的画像"},
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}
    body = client.get("/api/v1/memory/profiles", params={"search": "人工纠正"}).json()
    assert body["total"] == 1


def test_update_profile_blank_text_422(client: TestClient) -> None:
    _seed_profiles(1)
    res = client.patch("/api/v1/memory/profiles/bilibili/u_0", json={"profile_text": "   "})
    assert res.status_code == 422


def test_update_profile_missing_404(client: TestClient) -> None:
    res = client.patch("/api/v1/memory/profiles/bilibili/nobody", json={"profile_text": "x"})
    assert res.status_code == 404


def test_delete_profile_and_missing_404(client: TestClient) -> None:
    _seed_profiles(1)
    assert client.delete("/api/v1/memory/profiles/bilibili/u_0").json() == {"success": True}
    assert client.delete("/api/v1/memory/profiles/bilibili/u_0").status_code == 404
    body = client.get("/api/v1/memory/profiles").json()
    assert body["total"] == 0


# ===== 事实列表 / 删除 =====


def test_facts_list_by_viewer(client: TestClient) -> None:
    _seed_facts(count=3)
    body = client.get("/api/v1/memory/facts", params={"platform": "bilibili", "user_id": "u_0"}).json()
    assert body["total"] == 3
    item = body["items"][0]
    assert set(item) == {
        "id",
        "platform",
        "user_id",
        "fact_text",
        "source_message_id",
        "created_at_ms",
    }


def test_facts_list_by_search(client: TestClient) -> None:
    _seed_facts(count=3)
    body = client.get("/api/v1/memory/facts", params={"search": "关键词1"}).json()
    assert body["total"] == 1
    assert body["items"][0]["fact_text"] == "事实条目1 关键词1"


def test_facts_list_requires_params(client: TestClient) -> None:
    """按人查与关键词都不给 → 422。"""
    res = client.get("/api/v1/memory/facts")
    assert res.status_code == 422


def test_delete_fact_and_missing_404(client: TestClient) -> None:
    _seed_facts(count=1)
    fact_id = client.get(
        "/api/v1/memory/facts", params={"platform": "bilibili", "user_id": "u_0"}
    ).json()["items"][0]["id"]
    assert client.delete(f"/api/v1/memory/facts/{fact_id}").json() == {"success": True}
    assert client.delete(f"/api/v1/memory/facts/{fact_id}").status_code == 404


# ===== 统计 =====


def test_stats(client: TestClient) -> None:
    empty = client.get("/api/v1/memory/stats").json()
    assert empty == {"profile_count": 0, "fact_count": 0, "latest_updated_ms": 0}

    _seed_profiles(2)
    _seed_facts(user_id="u_1", count=2)
    body = client.get("/api/v1/memory/stats").json()
    assert body["profile_count"] == 2
    assert body["fact_count"] == 2


# ===== 记忆栈未装配 =====


def test_memory_unavailable_503(monkeypatch: pytest.MonkeyPatch, temp_db_path: Path) -> None:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    async def _build():
        bus = EventBus()
        server = DashboardServer(
            event_bus=bus,
            config_service=None,  # type: ignore[arg-type]
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60236),
            memory=None,
        )
        return bus, server

    loop = asyncio.new_event_loop()
    bus, server = loop.run_until_complete(_build())
    loop.close()

    set_dashboard_server(server)
    try:
        app = create_app()
        with TestClient(app) as c:
            res = c.get("/api/v1/memory/profiles")
            assert res.status_code == 503
            assert c.get("/api/v1/memory/stats").status_code == 503
    finally:
        set_dashboard_server(None)
        _run(bus.cleanup())
