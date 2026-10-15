"""Memory API 测试：常客名册管理端点（名册列表 / 纠正 / 删除 / 统计）。

使用真实 SQLiteDatabase（临时库）经 DashboardServer 挂载 ``ViewerProfileRepo``，
走完整 HTTP 层；造数直连仓储写入方法（与主播 Agent 收播写库同链路）。
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
from src.modules.storage.repos import ViewerProfileRepo


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


def _build_server(*, port: int, profile_repo: ViewerProfileRepo | None) -> tuple[EventBus, object]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.server import DashboardServer

    bus = EventBus()
    server = DashboardServer(
        event_bus=bus,
        config_service=None,  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(host="127.0.0.1", port=port),
        viewer_profile_repo=profile_repo,
    )
    return bus, server


@pytest.fixture
def client(temp_db_path: Path) -> Generator[TestClient, None, None]:
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    store = SQLiteDatabase(temp_db_path)
    _run(store.initialize())
    bus, server = _build_server(port=60235, profile_repo=store.viewer_profiles)

    _server_ref_cache["store"] = store
    set_dashboard_server(server)
    app = create_app()
    with TestClient(app) as c:
        yield c

    set_dashboard_server(None)
    _run(bus.cleanup())
    _run(store.close())


def _seed_profiles(count: int) -> None:
    """直连仓储造印象（user_id 递增，文本含可搜索关键词）。"""

    async def _seed_async():
        repo: ViewerProfileRepo = _server_ref_cache["store"].viewer_profiles
        for i in range(count):
            await repo.upsert_profile(
                platform="bilibili",
                user_id=f"u_{i}",
                profile_text=f"印象{i}：喜欢Minecraft" if i % 2 else f"印象{i}：喜欢恐怖游戏",
                last_compressed_at_ms=1_000 + i,
            )

    _run(_seed_async())


# ===== 名册列表：字段 / 搜索 / 分页 =====


def test_profiles_empty(client: TestClient) -> None:
    assert client.get("/api/v1/memory/profiles").json() == {"total": 0, "items": []}


def test_profiles_item_shape(client: TestClient) -> None:
    _seed_profiles(1)
    body = client.get("/api/v1/memory/profiles").json()
    assert body["total"] == 1
    item = body["items"][0]
    assert set(item) == {"platform", "user_id", "profile_text", "last_compressed_at_ms", "updated_at_ms"}
    assert item["user_id"] == "u_0"
    assert item["last_compressed_at_ms"] == 1_000


def test_profiles_search_and_pagination(client: TestClient) -> None:
    _seed_profiles(5)
    body = client.get("/api/v1/memory/profiles", params={"search": "Minecraft"}).json()
    assert body["total"] == 2
    assert all("Minecraft" in item["profile_text"] for item in body["items"])

    page1 = client.get("/api/v1/memory/profiles", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/v1/memory/profiles", params={"limit": 2, "offset": 2}).json()
    assert page1["total"] == 5 and len(page1["items"]) == 2
    assert {i["user_id"] for i in page1["items"]}.isdisjoint({i["user_id"] for i in page2["items"]})


# ===== 印象纠正 / 删除 =====


def test_update_profile_text(client: TestClient) -> None:
    _seed_profiles(1)
    res = client.patch("/api/v1/memory/profiles/bilibili/u_0", json={"profile_text": "人工纠正后的印象"})
    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert client.get("/api/v1/memory/profiles", params={"search": "人工纠正"}).json()["total"] == 1


def test_update_profile_blank_and_missing(client: TestClient) -> None:
    _seed_profiles(1)
    assert client.patch("/api/v1/memory/profiles/bilibili/u_0", json={"profile_text": "   "}).status_code == 422
    assert client.patch("/api/v1/memory/profiles/bilibili/nobody", json={"profile_text": "x"}).status_code == 404


def test_delete_profile_and_missing_404(client: TestClient) -> None:
    _seed_profiles(1)
    assert client.delete("/api/v1/memory/profiles/bilibili/u_0").json() == {"success": True}
    assert client.delete("/api/v1/memory/profiles/bilibili/u_0").status_code == 404
    assert client.get("/api/v1/memory/profiles").json()["total"] == 0


def test_fact_endpoints_retired(client: TestClient) -> None:
    """观众事实中间层已退役：事实端点不再存在。"""
    assert client.get("/api/v1/memory/facts", params={"search": "x"}).status_code == 404


# ===== 统计 =====


def test_stats(client: TestClient) -> None:
    assert client.get("/api/v1/memory/stats").json() == {"profile_count": 0, "latest_updated_ms": 0}
    _seed_profiles(2)
    body = client.get("/api/v1/memory/stats").json()
    assert body["profile_count"] == 2
    assert body["latest_updated_ms"] > 0


# ===== 名册未装配 =====


def test_memory_unavailable_503() -> None:
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server

    bus, server = _build_server(port=60236, profile_repo=None)
    set_dashboard_server(server)
    try:
        with TestClient(create_app()) as c:
            assert c.get("/api/v1/memory/profiles").status_code == 503
            assert c.get("/api/v1/memory/stats").status_code == 503
    finally:
        set_dashboard_server(None)
        _run(bus.cleanup())
