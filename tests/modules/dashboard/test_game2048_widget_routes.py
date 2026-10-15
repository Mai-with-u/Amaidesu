"""widget 族路由 game2048 端点：透明页开关、HTTP 查询、WS 连接首帧（单槽 history）"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.dashboard.widget.game2048_service import Game2048WidgetService
from src.modules.dashboard.widget.page import GAME2048_HTML
from src.modules.dashboard.widget.routes import WidgetGateway, create_widget_router
from src.modules.events.payloads.game import GameBoardStatePayload

_STATE = GameBoardStatePayload(
    game="game_2048",
    board=[[2, 0, 0, 0], [0, 4, 0, 0], [0, 0, 0, 0], [0, 0, 0, 2]],
    score=128,
    moves=3,
    max_tile=4,
    over=False,
)


def _client(*, with_service: bool = False, with_state: bool = False, with_page: bool = False):
    gateway = WidgetGateway()
    if with_service:
        # 直接置单槽快照（不经 EventBus 订阅——服务层语义已由专测覆盖）
        gateway.game2048_service = Game2048WidgetService.__new__(Game2048WidgetService)
        gateway.game2048_service._latest = _STATE.model_dump(mode="json") if with_state else None
    app = FastAPI()
    app.include_router(create_widget_router(gateway, include_page=False, include_game2048_page=with_page))
    return TestClient(app), gateway


def test_page_is_served_only_when_enabled_while_the_http_query_stays_open() -> None:
    page = _client(with_page=True)[0].get("/widget/2048")
    assert page.status_code == 200 and page.text == GAME2048_HTML and "/ws/game2048" in page.text

    client, _ = _client()
    assert client.get("/widget/2048").status_code == 404
    assert client.get("/api/widget/game2048").status_code == 200  # 呈现数据通道不受页面开关影响


def test_http_query_returns_the_latest_state_or_none_without_a_service() -> None:
    body = _client(with_service=True, with_state=True)[0].get("/api/widget/game2048").json()
    assert (body["state"]["game"], body["state"]["moves"]) == ("game_2048", 3)

    assert _client()[0].get("/api/widget/game2048").json() == {"state": None}


def test_ws_pushes_the_latest_state_on_connect_and_stays_silent_without_a_snapshot() -> None:
    client, _ = _client(with_service=True, with_state=True)
    with client.websocket_connect("/ws/game2048") as ws:
        frame = ws.receive_json()
        assert (frame["type"], frame["state"]["game"], frame["state"]["moves"]) == ("state", "game_2048", 3)

    client, gateway = _client(with_service=True)
    with client.websocket_connect("/ws/game2048"):  # 无快照：不推首帧（receive 会挂起，故只验证连接已注册）
        assert len(gateway._game2048_clients) == 1
