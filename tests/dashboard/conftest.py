"""Dashboard 测试共用装配：把组件挂到 DashboardServer，返回指向 FastAPI 应用的 HTTP 客户端。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from src.modules.config.core_schemas import DashboardConfig
from src.modules.config.service import ConfigService
from src.modules.dashboard.api.router import create_app
from src.modules.dashboard.dependencies import set_dashboard_server
from src.modules.dashboard.server import DashboardServer


def build_server(**components: Any) -> DashboardServer:
    components.setdefault("event_bus", None)
    components.setdefault("config_service", None)
    return DashboardServer(dashboard_config=DashboardConfig(host="127.0.0.1", port=60214), **components)


@pytest.fixture
def initialized_config(tmp_path: Path) -> ConfigService:
    """首启生成七文件基线的配置服务（tmp_path/config）。"""
    (tmp_path / "config").mkdir(exist_ok=True)
    service = ConfigService(base_dir=str(tmp_path))
    service.initialize()
    return service


@pytest.fixture
def mount():
    """同步版：mount(**components) -> TestClient；测试结束摘除注入点。"""

    def _mount(**components: Any) -> TestClient:
        set_dashboard_server(build_server(**components))
        return TestClient(create_app())

    yield _mount
    set_dashboard_server(None)  # type: ignore[arg-type]


@pytest.fixture
async def api():
    """异步版：await api(**components) -> httpx.AsyncClient，造数与请求共用测试的事件循环。"""
    clients: list[httpx.AsyncClient] = []

    async def _mount(**components: Any) -> httpx.AsyncClient:
        set_dashboard_server(build_server(**components))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://dashboard")
        clients.append(client)
        return client

    yield _mount
    for client in clients:
        await client.aclose()
    set_dashboard_server(None)  # type: ignore[arg-type]


@pytest.fixture
async def session_manager(store, event_bus):
    """已启动的 LiveSessionManager（真实仓储 + 事件总线）；结束时收掉进行中的场次。"""
    from src.modules.session import LiveSessionManager

    manager = LiveSessionManager(store.sessions, store.chat, event_bus)
    await manager.start()
    yield manager
    if manager.active_pk is not None:
        await manager.close_session()
