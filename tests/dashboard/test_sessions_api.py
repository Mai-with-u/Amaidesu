"""Sessions API 测试：场次列表 / 开启 / 结束 / 删除。

使用真实 LiveSessionManager（tmp SQLiteStore + EventBus），走完整 HTTP 层。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from src.modules.events.event_bus import EventBus
from src.modules.session import LiveSessionManager
from src.modules.storage import SQLiteStore


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="sessions-api-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
def client(temp_db_path: Path) -> Generator[TestClient, None, None]:
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer

    async def _build():
        store = SQLiteStore(temp_db_path)
        await store.initialize()
        bus = EventBus()
        manager = LiveSessionManager(store, bus)
        await manager.start()

        server = DashboardServer(
            event_bus=bus,
            context_service=None,  # type: ignore[arg-type]
            config_service=None,  # type: ignore[arg-type]
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
            session_manager=manager,
        )
        return store, bus, manager, server

    import asyncio

    loop = asyncio.new_event_loop()
    store, bus, manager, server = loop.run_until_complete(_build())
    loop.close()

    set_dashboard_server(server)
    app = create_app()
    with TestClient(app) as c:
        yield c

    set_dashboard_server(None)

    async def _teardown():
        if manager.active_pk is not None:
            await manager.close_session()
        await bus.cleanup()
        await store.close()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_teardown())
    loop.close()


def test_open_list_close_flow(client: TestClient) -> None:
    # 初始：只有临时兜底场次
    body = client.get("/api/v1/live-sessions").json()
    assert body["active_session_id"] is None
    assert any(item["source"] == "scratch" for item in body["items"])

    # 开启
    opened = client.post("/api/v1/live-sessions/open", json={"title": "晚间场"}).json()
    pk = opened["live_session_id"]
    assert pk > 0

    # 列表显示进行中场次
    body = client.get("/api/v1/live-sessions").json()
    assert body["active_session_id"] == pk
    active = [item for item in body["items"] if item["is_active"]]
    assert len(active) == 1
    assert active[0]["title"] == "晚间场"
    assert active[0]["ended_at_ms"] is None

    # 空场次结束 → 整行丢弃（防膨胀）
    closed = client.post(f"/api/v1/live-sessions/{pk}/close").json()
    assert closed["success"] is True
    body = client.get("/api/v1/live-sessions").json()
    assert body["active_session_id"] is None
    assert all(item["live_session_id"] != pk for item in body["items"])


def test_close_mismatched_session_returns_409(client: TestClient) -> None:
    client.post("/api/v1/live-sessions/open", json={})
    other = client.post("/api/v1/live-sessions/open", json={}).json()["live_session_id"]
    # 开第二个场次时第一个已被自动结束；指定一个已结束的 id → 409
    stale = client.get("/api/v1/live-sessions").json()["active_session_id"]
    r = client.post(f"/api/v1/live-sessions/{stale + 100000}/close")
    assert r.status_code == 409


def test_delete_session(client: TestClient) -> None:
    opened = client.post("/api/v1/live-sessions/open", json={"title": "待删"}).json()
    pk = opened["live_session_id"]
    r = client.delete(f"/api/v1/live-sessions/{pk}")
    assert r.status_code == 200
    assert r.json()["success"] is True
    # 再删 → 404
    r = client.delete(f"/api/v1/live-sessions/{pk}")
    assert r.status_code == 404
