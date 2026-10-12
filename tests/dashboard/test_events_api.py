"""Events API 测试：游标缺口拉取的参数契约。

前端 events store 固定以 limit=MAX_EVENTS(1000) 调 GET /events?since_id=，
后端 limit 上限低于该值即整请求 422——控制台时间线断线/刷新补缺口全体失败。
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
from src.modules.storage.database import SQLiteDatabase


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """最小装配（无场次管理器也可读事件历史），走完整 HTTP 层。"""
    from src.modules.config.core_schemas import DashboardConfig
    from src.modules.dashboard.api.router import create_app
    from src.modules.dashboard.dependencies import set_dashboard_server
    from src.modules.dashboard.server import DashboardServer
    from src.modules.events.event_history import EventHistoryService

    td = Path(tempfile.mkdtemp(prefix="events-api-"))
    store = SQLiteDatabase(td / "test.db")

    async def _build():
        await store.initialize()
        bus = EventBus()
        manager = LiveSessionManager(store.sessions, store.chat, bus)
        await manager.start()
        server = DashboardServer(
            event_bus=bus,
            config_service=None,  # type: ignore[arg-type]
            dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
            session_manager=manager,
            event_history=EventHistoryService(max_events=100),
        )
        return bus, manager, server

    import asyncio

    loop = asyncio.new_event_loop()
    bus, manager, server = loop.run_until_complete(_build())
    loop.close()

    set_dashboard_server(server)
    app = create_app()
    with TestClient(app) as c:
        yield c

    set_dashboard_server(None)

    async def _teardown():
        await bus.cleanup()
        await store.close()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_teardown())
    loop.close()
    shutil.rmtree(td, ignore_errors=True)


def test_cursor_gap_fetch_with_limit_1000_is_accepted(client: TestClient) -> None:
    """前端契约形状（since_id + limit=1000）必须 200，而非 422。"""
    body = client.get("/api/v1/events", params={"since_id": "1962", "limit": 1000}).json()
    assert "events" in body and "total" in body


def test_limit_upper_bound_is_1000(client: TestClient) -> None:
    """上限边界：1000 合法、1001 拒绝。"""
    assert client.get("/api/v1/events", params={"limit": 1000}).status_code == 200
    assert client.get("/api/v1/events", params={"limit": 1001}).status_code == 422
