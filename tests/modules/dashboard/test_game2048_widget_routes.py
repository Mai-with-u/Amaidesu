"""widget 族路由 game2048 端点测试（透明页 / WS 单槽 history / HTTP 查询）"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.dashboard.widget.game2048_service import Game2048WidgetService
from src.modules.dashboard.widget.page import GAME2048_HTML
from src.modules.dashboard.widget.routes import WidgetGateway, create_widget_router
from src.modules.events.payloads.game import GameBoardStatePayload


def _make_state(moves: int = 3) -> GameBoardStatePayload:
    return GameBoardStatePayload(
        game="game_2048",
        board=[[2, 0, 0, 0], [0, 4, 0, 0], [0, 0, 0, 0], [0, 0, 0, 2]],
        score=128,
        moves=moves,
        max_tile=4,
        over=False,
    )


def _make_app(*, with_service: bool, with_state: bool, with_page: bool) -> tuple[FastAPI, WidgetGateway]:
    app = FastAPI()
    gateway = WidgetGateway()
    if with_service:
        gateway.game2048_service = Game2048WidgetService.__new__(Game2048WidgetService)
        # 直接置单槽快照（不经 EventBus 订阅——服务层语义已由专测覆盖）
        gateway.game2048_service._latest = (  # noqa: SLF001 - 测试直填快照槽
            _make_state().model_dump(mode="json") if with_state else None
        )
    app.include_router(create_widget_router(gateway, include_page=False, include_game2048_page=with_page))
    return app, gateway


def test_game2048_page_served_when_enabled() -> None:
    app, _ = _make_app(with_service=False, with_state=False, with_page=True)
    client = TestClient(app)
    resp = client.get("/widget/2048")
    assert resp.status_code == 200
    assert resp.text == GAME2048_HTML
    assert "/ws/game2048" in resp.text


def test_game2048_page_absent_when_disabled() -> None:
    app, _ = _make_app(with_service=False, with_state=False, with_page=False)
    client = TestClient(app)
    assert client.get("/widget/2048").status_code == 404
    # WS 与 HTTP 查询不受页面开关影响（呈现数据通道常开）
    assert client.get("/api/widget/game2048").status_code == 200


def test_game2048_http_query_shape() -> None:
    app, _ = _make_app(with_service=True, with_state=True, with_page=False)
    client = TestClient(app)
    body = client.get("/api/widget/game2048").json()
    assert body["state"]["game"] == "game_2048"
    assert body["state"]["moves"] == 3


def test_game2048_http_query_empty_when_no_service() -> None:
    app, _ = _make_app(with_service=False, with_state=False, with_page=False)
    client = TestClient(app)
    assert client.get("/api/widget/game2048").json() == {"state": None}


def test_game2048_ws_sends_latest_state_on_connect() -> None:
    app, _ = _make_app(with_service=True, with_state=True, with_page=False)
    client = TestClient(app)
    with client.websocket_connect("/ws/game2048") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "state"
        assert frame["state"]["game"] == "game_2048"
        assert frame["state"]["moves"] == 3


def test_game2048_ws_no_frame_without_state() -> None:
    """无快照时接入不推首帧（连接建立即静默等待事件）。"""
    app, gateway = _make_app(with_service=True, with_state=False, with_page=False)
    client = TestClient(app)
    with client.websocket_connect("/ws/game2048"):
        # 有连接即通过：无首帧可收（receive 会挂起，故仅验证连接成功）
        assert len(gateway._game2048_clients) == 1  # noqa: SLF001 - 连接注册断言
