"""widget 族路由：WS 统一前缀下的连接首帧、danmaku 样式下发与旧路径退役断言"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.dashboard.widget.game2048_service import Game2048WidgetService
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


def test_danmaku_socket_sends_history_then_configured_style() -> None:
    client, _ = _client(with_danmaku=True)
    with client.websocket_connect("/ws/widget/danmaku") as ws:
        history = ws.receive_json()
        assert history["type"] == "history" and history["messages"] == []
        style = ws.receive_json()
        assert style == {"type": "style", "font_size": 16}  # 默认配置字号


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
