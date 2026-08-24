"""debug 注入接口消息类型白名单测试。

覆盖 ``POST /api/v1/debug/inject-message``：
1. 未登记 data_type → 400（JSON 错误体含类型名）
2. 已登记 data_type → 成功注入（事件发布）
3. event_bus 缺失时优雅降级（success=False，不抛 500）

v2 适配说明：端点内部已改发 RoomMessagePayload（语义域事件），
本测试只验证 HTTP 契约，与 payload 类型解耦。
"""

import pytest
from fastapi.testclient import TestClient

from src.modules.config.core_schemas import DashboardConfig
from src.modules.dashboard.api.router import create_app
from src.modules.dashboard.dependencies import set_dashboard_server
from src.modules.dashboard.server import DashboardServer
from src.modules.events.event_bus import EventBus


def _make_server(event_bus) -> DashboardServer:
    return DashboardServer(
        event_bus=event_bus,
        context_service=None,  # type: ignore[arg-type]
        config_service=None,  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
    )


@pytest.fixture
def client():
    server = _make_server(EventBus())
    set_dashboard_server(server)
    app = create_app()
    try:
        yield TestClient(app)
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]


def _inject_payload(data_type: str) -> dict:
    return {"text": "测试弹幕", "data_type": data_type, "source": "test_debug"}


def test_unregistered_data_type_returns_400(client):
    resp = client.post("/api/v1/debug/inject-message", json=_inject_payload("not_registered_xyz"))
    assert resp.status_code == 400
    assert "not_registered_xyz" in resp.json()["detail"]


def test_registered_data_type_injects_successfully(client):
    resp = client.post("/api/v1/debug/inject-message", json=_inject_payload("text"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["message_id"]


def test_event_bus_missing_returns_success_false():
    server = _make_server(None)
    set_dashboard_server(server)
    app = create_app()
    try:
        resp = TestClient(app).post("/api/v1/debug/inject-message", json=_inject_payload("text"))
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "Event bus not available" in resp.json()["error"]
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]
