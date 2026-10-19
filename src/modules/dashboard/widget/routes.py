"""弹幕/字幕小部件网关

widget 族的客户端集合、广播与 WS 收发循环，以及 3 条 WS 路由。
``WidgetGateway`` 由 DashboardServer 构造时持有（不做模块级单例），
路由经 ``create_widget_router`` 工厂挂载。
"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.modules.dashboard.widget import DanmakuWidgetService
from src.modules.dashboard.widget.game2048_service import Game2048WidgetService
from src.modules.logging import get_logger

logger = get_logger("WidgetGateway")


class WidgetGateway:
    """widget 族 WebSocket 客户端集合与广播收发。"""

    def __init__(self) -> None:
        self.widget_service: Optional[DanmakuWidgetService] = None
        self.game2048_service: Optional[Game2048WidgetService] = None
        self._danmaku_clients: set[WebSocket] = set()
        self._subtitle_clients: set[WebSocket] = set()
        self._game2048_clients: set[WebSocket] = set()

    async def stop(self) -> None:
        """停止小部件服务并关闭全部客户端连接。"""
        if self.widget_service:
            await self.widget_service.stop()
            self.widget_service = None
        if self.game2048_service:
            await self.game2048_service.stop()
            self.game2048_service = None

        all_clients = list(self._danmaku_clients) + list(self._subtitle_clients) + list(self._game2048_clients)
        for client in all_clients:
            try:
                await client.close()
            except Exception as e:
                logger.debug(f"关闭 widget WebSocket 客户端失败（已忽略）: {e}")
        self._danmaku_clients.clear()
        self._subtitle_clients.clear()
        self._game2048_clients.clear()

    def reset(self) -> None:
        """释放服务引用并清空全部客户端集合（不主动关连接）。"""
        self.widget_service = None
        self.game2048_service = None
        self._danmaku_clients.clear()
        self._subtitle_clients.clear()
        self._game2048_clients.clear()

    async def run_danmaku_socket(self, websocket: WebSocket) -> None:
        style_payload: Optional[dict] = None
        if self.widget_service is not None:
            style_payload = {"type": "style", "font_size": self.widget_service.config.font_size}
        await self._run_socket(websocket, self._danmaku_clients, with_history=True, style_payload=style_payload)

    async def run_subtitle_socket(self, websocket: WebSocket) -> None:
        await self._run_socket(websocket, self._subtitle_clients, with_history=False)

    async def run_game2048_socket(self, websocket: WebSocket) -> None:
        """2048 棋盘 WS 收发循环：接入即推单槽最新快照，后续随事件增量推送。"""
        await websocket.accept()
        self._game2048_clients.add(websocket)

        try:
            if self.game2048_service is not None:
                latest = self.game2048_service.get_latest_state()
                if latest is not None:
                    await websocket.send_json({"type": "state", "state": latest})

            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            # 关闭信号：静默退出（finally 负责清理 client 集合）
            pass
        except Exception as e:
            logger.debug(f"Game2048 WebSocket 错误: {e}")
        finally:
            self._game2048_clients.discard(websocket)

    async def _run_socket(
        self,
        websocket: WebSocket,
        clients: set[WebSocket],
        *,
        with_history: bool,
        style_payload: Optional[dict] = None,
    ) -> None:
        """widget 族 WebSocket 端点的公共收发循环（accept → 可选历史/样式 → 保活 → 清理）。"""
        await websocket.accept()
        clients.add(websocket)

        try:
            if with_history and self.widget_service is not None:
                history = self.widget_service.get_recent_messages(15)
                await websocket.send_json({"type": "history", "messages": history})

            if style_payload:
                await websocket.send_json(style_payload)

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

    async def broadcast_game2048(self, data: dict) -> None:
        """广播棋盘状态到所有 game2048 客户端"""
        await self._broadcast_to_clients(self._game2048_clients, data)


def create_widget_router(gateway: WidgetGateway) -> APIRouter:
    """装配 widget 族 WS 路由（各 widget 开关独立挂载，任一启用即挂）。

    嵌入页本身是前端 SPA 的 /widget/<name> 路由（由 serve_spa 兜底服务），
    这里只挂它们消费的 WS 通道。
    """
    router = APIRouter()

    @router.websocket("/ws/widget/danmaku")
    async def danmaku_websocket(websocket: WebSocket) -> None:
        await gateway.run_danmaku_socket(websocket)

    @router.websocket("/ws/widget/subtitle")
    async def subtitle_websocket(websocket: WebSocket) -> None:
        await gateway.run_subtitle_socket(websocket)

    @router.websocket("/ws/widget/game2048")
    async def game2048_websocket(websocket: WebSocket) -> None:
        await gateway.run_game2048_socket(websocket)

    return router
