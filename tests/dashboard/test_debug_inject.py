"""debug 注入接口契约测试。

覆盖 ``POST /api/v1/debug/inject-message``：
1. 注入成功 → 发布 room.message.danmaku，payload 恒带 ``simulated=True``
   （手动注入属模拟数据，不得以真实观众身份落库污染统计）
2. source 承载观众昵称（user.id/user.name 同源），message_id 与响应一致可对账
3. event_bus 缺失时优雅降级（success=False，不抛 500）
"""

from fastapi.testclient import TestClient

from src.modules.config.core_schemas import DashboardConfig
from src.modules.dashboard.api.router import create_app
from src.modules.dashboard.dependencies import set_dashboard_server
from src.modules.dashboard.server import DashboardServer
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import RoomMessagePayload


class _RecordingBus:
    """记录 emit 调用的假总线（注入端点只依赖 ``emit``，同步记录无时序问题）。"""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, RoomMessagePayload, str]] = []

    async def emit(self, event_name: str, data: RoomMessagePayload, source: str = "unknown") -> None:
        self.emitted.append((event_name, data, source))


def _make_server(event_bus) -> DashboardServer:
    return DashboardServer(
        event_bus=event_bus,
        config_service=None,  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(host="127.0.0.1", port=60214),
    )


def test_inject_publishes_simulated_danmaku():
    bus = _RecordingBus()
    set_dashboard_server(_make_server(bus))
    app = create_app()
    try:
        resp = TestClient(app).post(
            "/api/v1/debug/inject-message",
            json={"text": "测试弹幕", "source": "测试观众"},
        )
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["message_id"]

    assert len(bus.emitted) == 1
    event_name, payload, source = bus.emitted[0]
    assert event_name == CoreEvents.ROOM_MESSAGE_DANMAKU
    # 手动注入恒为模拟数据：统计查询排除 simulated，防污染观众口径
    assert payload.simulated is True
    assert payload.message_type == "danmaku"
    assert payload.content == "测试弹幕"
    assert payload.user.id == "测试观众"
    assert payload.user.name == "测试观众"
    # message_id 与响应回传同一 ID（"注入 → 决策 → 回复"可对账）
    assert payload.message_id == body["message_id"]
    assert source == "dashboard.debug"


def test_event_bus_missing_returns_success_false():
    server = _make_server(None)
    set_dashboard_server(server)
    app = create_app()
    try:
        resp = TestClient(app).post(
            "/api/v1/debug/inject-message",
            json={"text": "测试弹幕", "source": "test_debug"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "Event bus not available" in resp.json()["error"]
    finally:
        set_dashboard_server(None)  # type: ignore[arg-type]
