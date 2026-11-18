"""widget 族路由：WS 统一前缀下的连接首帧、danmaku 样式下发与旧路径退役断言"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.dashboard.widget.game2048_service import Game2048WidgetService
from src.modules.dashboard.widget.models import DanmakuWidgetConfig, DanmakuWidgetMessage, MessageType
from src.modules.dashboard.widget.routes import WidgetGateway, create_widget_router
from src.modules.dashboard.widget.service import DanmakuWidgetService
from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.game import GameBoardStatePayload

_STATE = GameBoardStatePayload(
    game="game_2048",
    board=[[2, 0, 0, 0], [0, 4, 0, 0], [0, 0, 0, 0], [0, 0, 0, 2]],
    score=128,
    moves=3,
    max_tile=4,
    over=False,
)


def _client(*, with_service: bool = False, with_state: bool = False, with_danmaku: bool = False):
    gateway = WidgetGateway()
    if with_service:
        # 直接置单槽快照（不经 EventBus 订阅——服务层语义已由专测覆盖）
        gateway.game2048_service = Game2048WidgetService.__new__(Game2048WidgetService)
        gateway.game2048_service._latest = _STATE.model_dump(mode="json") if with_state else None
    if with_danmaku:
        gateway.widget_service = DanmakuWidgetService(event_bus=EventBus())
    app = FastAPI()
    app.include_router(create_widget_router(gateway))
    return TestClient(app), gateway


def test_ws_pushes_the_latest_state_on_connect_and_stays_silent_without_a_snapshot() -> None:
    client, _ = _client(with_service=True, with_state=True)
    with client.websocket_connect("/ws/widget/game2048") as ws:
        frame = ws.receive_json()
        assert (frame["type"], frame["state"]["game"], frame["state"]["moves"]) == ("state", "game_2048", 3)

    client, gateway = _client(with_service=True)
    with client.websocket_connect("/ws/widget/game2048"):  # 无快照：不推首帧（receive 会挂起，故只验证连接已注册）
        assert len(gateway._game2048_clients) == 1


def test_danmaku_socket_sends_style_before_history_with_configured_limits() -> None:
    """首帧序列 style → history：前端须先拿到缓冲上限再收历史，否则配置超默认上限的部分被截断。"""
    client, _ = _client(with_danmaku=True)
    with client.websocket_connect("/ws/widget/danmaku") as ws:
        style = ws.receive_json()
        # 默认配置：字号 16、条数上限 30
        assert style == {"type": "style", "font_size": 16, "max_messages": 30}
        history = ws.receive_json()
        assert history["type"] == "history" and history["messages"] == []


def test_danmaku_history_count_follows_configured_max_messages() -> None:
    """握手历史条数取配置 max_messages，不再硬编码 15。"""
    config = DanmakuWidgetConfig(max_messages=5)
    service = DanmakuWidgetService(event_bus=EventBus(), config=config)
    for i in range(8):
        service.messages.append(DanmakuWidgetMessage(user_name=f"u{i}", content=str(i), message_type=MessageType.TEXT))
    gateway = WidgetGateway()
    gateway.widget_service = service
    app = FastAPI()
    app.include_router(create_widget_router(gateway))
    client = TestClient(app)
    with client.websocket_connect("/ws/widget/danmaku") as ws:
        ws.receive_json()  # style 在前
        history = ws.receive_json()
        # 8 条缓冲只按配置上限回放最近 5 条
        assert [m["content"] for m in history["messages"]] == ["3", "4", "5", "6", "7"]


def test_retired_legacy_page_and_query_routes_are_gone() -> None:
    """旧透明页与其 HTTP 查询端点随退役整体移除，不再可达。"""
    client, _ = _client()
    assert client.get("/widget/2048").status_code == 404
    assert client.get("/api/widget/game2048").status_code == 404


def test_retired_flat_ws_paths_are_gone() -> None:
    """WS 统一 /ws/widget/<name> 前缀后，旧平铺路径不再有效（HTTP 探测即框架拒绝）。"""
    client, _ = _client(with_danmaku=True)
    for path in ("/ws/danmaku", "/ws/subtitle", "/ws/game2048", "/ws/widget"):
        assert client.get(path).status_code in (403, 404, 405)
