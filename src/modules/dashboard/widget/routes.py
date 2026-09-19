"""弹幕/字幕小部件网关

widget 族的客户端集合、广播与 WS 收发循环，以及 6 条 HTTP/WS 路由。
``WidgetGateway`` 由 DashboardServer 构造时持有（不做模块级单例），
路由经 ``create_widget_router`` 工厂挂载。
"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from src.modules.dashboard.widget import DanmakuWidgetService
from src.modules.dashboard.widget.page import WIDGET_HTML
from src.modules.logging import get_logger

logger = get_logger("WidgetGateway")


class WidgetGateway:
    """widget 族 WebSocket 客户端集合与广播收发。"""

    def __init__(self) -> None:
        self.widget_service: Optional[DanmakuWidgetService] = None
        self._widget_clients: set[WebSocket] = set()
        self._danmaku_clients: set[WebSocket] = set()
        self._subtitle_clients: set[WebSocket] = set()

    async def stop(self) -> None:
        """停止小部件服务并关闭全部三组客户端连接。"""
        if self.widget_service:
            await self.widget_service.stop()
            self.widget_service = None

        for client in list(self._widget_clients) + list(self._danmaku_clients) + list(self._subtitle_clients):
            try:
                await client.close()
            except Exception as e:
                logger.debug(f"关闭 widget WebSocket 客户端失败（已忽略）: {e}")
        self._widget_clients.clear()
        self._danmaku_clients.clear()
        self._subtitle_clients.clear()

    def reset(self) -> None:
        """释放服务引用并清空全部客户端集合（不主动关连接）。"""
        self.widget_service = None
        self._widget_clients.clear()
        self._danmaku_clients.clear()
        self._subtitle_clients.clear()

    async def run_danmaku_socket(self, websocket: WebSocket) -> None:
        await self._run_socket(websocket, self._danmaku_clients, with_history=True)

    async def run_subtitle_socket(self, websocket: WebSocket) -> None:
        await self._run_socket(websocket, self._subtitle_clients, with_history=False)

    async def run_widget_socket(self, websocket: WebSocket) -> None:
        await self._run_socket(websocket, self._widget_clients, with_history=True)

    async def _run_socket(self, websocket: WebSocket, clients: set[WebSocket], *, with_history: bool) -> None:
        """widget 族 WebSocket 端点的公共收发循环（accept → 可选历史 → 保活 → 清理）。"""
        await websocket.accept()
        clients.add(websocket)

        try:
            if with_history and self.widget_service is not None:
                history = self.widget_service.get_recent_messages(15)
                await websocket.send_json({"type": "history", "messages": history})

            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            # 关闭信号：静默退出（finally 负责清理 client 集合）
            pass
        except Exception as e:
            logger.debug(f"Widget WebSocket 错误: {e}")
        finally:
            clients.discard(websocket)

    async def _broadcast_to_clients(self, clients: set[WebSocket], data: dict) -> None:
        """广播消息到一组 widget 客户端，发送失败的连接被移出集合。"""
        if not clients:
            return

        message = json.dumps(data, ensure_ascii=False, default=str)
        disconnected = set()

        for client in clients:
            try:
                await client.send_text(message)
            except Exception as e:
                logger.debug(f"广播时客户端已断开: {e}")
                disconnected.add(client)

        clients -= disconnected

    async def broadcast_danmaku(self, data: dict) -> None:
        """广播弹幕消息到所有 danmaku 客户端"""
        await self._broadcast_to_clients(self._danmaku_clients, data)

    async def broadcast_subtitle(self, data: dict) -> None:
        """广播字幕到所有 subtitle 客户端"""
        await self._broadcast_to_clients(self._subtitle_clients, data)


def create_widget_router(gateway: WidgetGateway, *, include_page: bool) -> APIRouter:
    """装配 widget 族路由（页面 + 3 条 WS + 3 条 HTTP 查询）。"""
    router = APIRouter()

    if include_page:

        @router.get("/widget", response_class=HTMLResponse)
        async def widget_page() -> HTMLResponse:
            return HTMLResponse(WIDGET_HTML)

    @router.websocket("/ws/danmaku")
    async def danmaku_websocket(websocket: WebSocket) -> None:
        await gateway.run_danmaku_socket(websocket)

    @router.websocket("/ws/subtitle")
    async def subtitle_websocket(websocket: WebSocket) -> None:
        await gateway.run_subtitle_socket(websocket)

    @router.websocket("/ws/widget")
    async def widget_websocket(websocket: WebSocket) -> None:
        await gateway.run_widget_socket(websocket)

    @router.get("/api/widget/messages")
    async def get_widget_messages() -> dict:
        if gateway.widget_service is None:
            return {"messages": []}
        return {"messages": gateway.widget_service.get_recent_messages(15)}

    @router.get("/api/widget/subtitles")
    async def get_widget_subtitles() -> dict:
        if gateway.widget_service is None:
            return {"subtitles": []}
        return {"subtitles": gateway.widget_service.get_recent_subtitles(5)}

    @router.get("/api/widget/stats")
    async def get_widget_stats() -> dict:
        if gateway.widget_service is None:
            return {"is_running": False}
        return gateway.widget_service.get_stats()

    return router
