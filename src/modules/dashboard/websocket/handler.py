"""
WebSocket 连接处理器

管理 WebSocket 连接的生命周期、订阅和心跳。
"""

import json
import time
import uuid
from typing import Callable, Dict, List, Set

from fastapi import WebSocket, WebSocketDisconnect

from src.modules.dashboard.schemas.event import (
    ClientInfo,
    SubscribeRequest,
    SubscribeResponse,
    WebSocketMessage,
)
from src.modules.logging import get_logger

logger = get_logger("WebSocketHandler")


class WebSocketHandler:
    """WebSocket 连接处理器"""

    def __init__(self, heartbeat_interval: int = 30):
        self.heartbeat_interval = heartbeat_interval
        self._clients: Dict[str, WebSocket] = {}
        self._client_info: Dict[str, ClientInfo] = {}
        self._client_subscriptions: Dict[str, Set[str]] = {}
        self._broadcast_callbacks: List[Callable] = []
        self._running = False

    @property
    def client_count(self) -> int:
        """当前连接的客户端数量"""
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> str:
        """接受新的 WebSocket 连接"""
        await websocket.accept()

        client_id = str(uuid.uuid4())
        self._clients[client_id] = websocket
        self._client_subscriptions[client_id] = set()
        self._client_info[client_id] = ClientInfo(
            client_id=client_id,
            connected_at=time.time(),
            subscribed_events=[],
        )

        logger.info(f"WebSocket 客户端连接: {client_id}，当前连接数: {self.client_count}")

        # 发送欢迎消息
        await self._send_to_client(
            client_id,
            WebSocketMessage(
                type="connected",
                timestamp=time.time(),
                data={
                    "client_id": client_id,
                    "heartbeat_interval": self.heartbeat_interval,
                },
            ),
        )

        return client_id

    async def disconnect(self, client_id: str) -> None:
        """断开客户端连接"""
        if client_id in self._clients:
            del self._clients[client_id]
        if client_id in self._client_subscriptions:
            del self._client_subscriptions[client_id]
        if client_id in self._client_info:
            del self._client_info[client_id]

        logger.info(f"WebSocket 客户端断开: {client_id}，当前连接数: {self.client_count}")

    async def handle_message(self, client_id: str, message: str) -> None:
        """处理客户端消息"""
        try:
            data = json.loads(message)

            # 处理订阅请求
            if "action" in data and "events" in data:
                request = SubscribeRequest(**data)
                await self._handle_subscribe(client_id, request)
            # 处理心跳响应
            elif data.get("type") == "pong":
                logger.debug(f"收到客户端 {client_id} 心跳响应")
            else:
                logger.warning(f"未知消息类型: {data}")

        except json.JSONDecodeError:
            logger.error(f"无效的 JSON 消息: {message}")
        except Exception as e:
            logger.error(f"处理消息失败: {e}")

    async def _handle_subscribe(self, client_id: str, request: SubscribeRequest) -> None:
        """处理订阅请求"""
        if client_id not in self._client_subscriptions:
            return

        if request.action == "subscribe":
            self._client_subscriptions[client_id].update(request.events)
            message = f"已订阅事件: {request.events}"
        elif request.action == "unsubscribe":
            self._client_subscriptions[client_id].difference_update(request.events)
            message = f"已取消订阅事件: {request.events}"
        else:
            message = f"未知操作: {request.action}"

        # 更新客户端信息
        self._client_info[client_id].subscribed_events = list(self._client_subscriptions[client_id])

        # 发送确认
        await self._send_to_client(
            client_id,
            WebSocketMessage(
                type="subscribe_response",
                timestamp=time.time(),
                data=SubscribeResponse(
                    success=True,
                    subscribed_events=list(self._client_subscriptions[client_id]),
                    message=message,
                ).model_dump(),
            ),
        )

    async def _send_to_client(self, client_id: str, message: WebSocketMessage) -> bool:
        """发送消息到指定客户端"""
        if client_id not in self._clients:
            return False

        try:
            await self._clients[client_id].send_text(message.model_dump_json())
            return True
        except Exception as e:
            logger.error(f"发送消息到客户端 {client_id} 失败: {e}")
            await self.disconnect(client_id)
            return False

    async def broadcast(self, event_type: str, data: Dict[str, object]) -> int:
        """广播消息到所有订阅了该事件的客户端"""
        message = WebSocketMessage(
            type=event_type,
            timestamp=time.time(),
            data=data,
        )

        success_count = 0
        disconnected = []

        for client_id, events in self._client_subscriptions.items():
            # 检查客户端是否订阅了该事件
            if event_type in events or "*" in events:
                if await self._send_to_client(client_id, message):
                    success_count += 1
                else:
                    disconnected.append(client_id)

        # 清理断开的连接
        for client_id in disconnected:
            await self.disconnect(client_id)

        return success_count

    async def send_heartbeat(self) -> None:
        """发送心跳到所有客户端"""
        message = WebSocketMessage(
            type="ping",
            timestamp=time.time(),
            data={},
        )

        for client_id in list(self._clients.keys()):
            await self._send_to_client(client_id, message)

    async def run_client_handler(self, websocket: WebSocket) -> None:
        """运行单个客户端的消息处理循环"""
        client_id = await self.connect(websocket)

        try:
            while True:
                message = await websocket.receive_text()
                await self.handle_message(client_id, message)
        except WebSocketDisconnect:
            logger.info(f"客户端 {client_id} 主动断开连接")
        except Exception as e:
            logger.error(f"客户端 {client_id} 连接异常: {e}")
        finally:
            await self.disconnect(client_id)

    def get_client_infos(self) -> List[ClientInfo]:
        """获取所有客户端信息"""
        return list(self._client_info.values())
