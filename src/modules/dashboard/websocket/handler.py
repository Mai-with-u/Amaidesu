"""
WebSocket 连接处理器

管理 WebSocket 连接的生命周期、订阅和心跳。
"""

import asyncio
import json
import time
import uuid
from typing import Awaitable, Callable, Dict, List, Optional, Set

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

    # 心跳超时倍数：连续 N 个心跳周期未收到 pong 视为僵尸连接并踢出
    HEARTBEAT_TIMEOUT_MULTIPLIER = 3

    # 单连接发送队列上限：满时丢弃最旧保新，防止慢客户端导致内存无限增长
    MAX_QUEUE_SIZE = 1024

    def __init__(self, heartbeat_interval: int = 30):
        self.heartbeat_interval = heartbeat_interval
        self._clients: Dict[str, WebSocket] = {}
        self._client_info: Dict[str, ClientInfo] = {}
        self._client_subscriptions: Dict[str, Set[str]] = {}
        self._last_pong: Dict[str, float] = {}
        self._send_queues: Dict[str, asyncio.Queue[WebSocketMessage]] = {}
        self._writer_tasks: Dict[str, "asyncio.Task[None]"] = {}
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
        self._last_pong[client_id] = time.time()

        # 每连接独立发送队列 + 单 writer task：串行发送，避免多协程并发写同一 socket
        queue: asyncio.Queue[WebSocketMessage] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._send_queues[client_id] = queue
        self._writer_tasks[client_id] = asyncio.create_task(self._writer_loop(client_id, websocket, queue))

        logger.info(f"WebSocket 客户端连接: {client_id}，当前连接数: {self.client_count}")

        # 发送欢迎消息（入队，由 writer task 串行发送）
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

    def _remove_client(self, client_id: str) -> None:
        """从注册表移除客户端（writer 异常清理与 disconnect 共用；不取消 writer task）"""
        self._clients.pop(client_id, None)
        self._client_subscriptions.pop(client_id, None)
        self._client_info.pop(client_id, None)
        self._last_pong.pop(client_id, None)

    async def disconnect(self, client_id: str) -> None:
        """断开客户端连接（取消 writer task 并清理注册表）"""
        task = self._writer_tasks.pop(client_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._send_queues.pop(client_id, None)
        self._remove_client(client_id)

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
                self._last_pong[client_id] = time.time()
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

    async def _writer_loop(self, client_id: str, websocket: WebSocket, queue: asyncio.Queue[WebSocketMessage]) -> None:
        """单连接写循环：唯一消费者，串行发送，避免并发写同一 socket。

        发送失败（socket 已坏）时清理注册表；disconnect() 通过取消本任务完成清理。
        """
        try:
            while True:
                message = await queue.get()
                await websocket.send_text(message.model_dump_json())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"发送消息到客户端 {client_id} 失败（将清理连接）: {e}")
            self._remove_client(client_id)

    async def _send_to_client(self, client_id: str, message: WebSocketMessage) -> bool:
        """将消息入队到客户端发送队列（非阻塞）。

        队列满时丢弃最旧的消息以保证新消息不丢失。返回 True 表示已入队
        （实际发送由 writer task 完成；发送失败由 writer 清理连接）。
        """
        queue = self._send_queues.get(client_id)
        if queue is None:
            return False

        try:
            queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            # 队列满：丢弃最旧一条后重试，保证新消息优先送达
            try:
                queue.get_nowait()
                queue.put_nowait(message)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
            return True

    async def broadcast(self, event_type: str, data: Dict[str, object], message_id: Optional[str] = None) -> int:
        """广播消息到所有订阅了该事件的客户端（入队，由各客户端 writer task 串行发送）"""
        message = WebSocketMessage(
            type=event_type,
            timestamp=time.time(),
            data=data,
            id=message_id,
        )

        success_count = 0

        # list() 快照：writer 异常清理可能并发修改注册表
        for client_id, events in list(self._client_subscriptions.items()):
            # 检查客户端是否订阅了该事件
            if event_type in events or "*" in events:
                if await self._send_to_client(client_id, message):
                    success_count += 1

        return success_count

    async def send_heartbeat(self) -> None:
        """发送心跳到所有客户端，并踢出超过超时阈值未响应 pong 的连接"""
        message = WebSocketMessage(
            type="ping",
            timestamp=time.time(),
            data={},
        )

        timeout = self.heartbeat_interval * self.HEARTBEAT_TIMEOUT_MULTIPLIER
        now = time.time()
        stale_clients: List[str] = []

        for client_id in list(self._clients.keys()):
            await self._send_to_client(client_id, message)
            if now - self._last_pong.get(client_id, 0.0) > timeout:
                stale_clients.append(client_id)

        for client_id in stale_clients:
            logger.warning(f"客户端 {client_id} 心跳超时（>{timeout}s 未响应 pong），断开连接")
            websocket = self._clients.get(client_id)
            await self.disconnect(client_id)
            if websocket is not None:
                try:
                    await websocket.close()
                except Exception as e:
                    logger.debug(f"关闭心跳超时连接失败（已忽略）: {e}")

    async def run_client_handler(
        self,
        websocket: WebSocket,
        on_connected: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        """运行单个客户端的消息处理循环。

        Args:
            websocket: 客户端 WebSocket 连接
            on_connected: 连接建立后、接收循环开始前的可选异步回调（如推送历史），
                接收 client_id 参数
        """
        client_id = await self.connect(websocket)

        try:
            if on_connected is not None:
                await on_connected(client_id)
            while True:
                message = await websocket.receive_text()
                await self.handle_message(client_id, message)
        except WebSocketDisconnect:
            logger.info(f"客户端 {client_id} 主动断开连接")
        except asyncio.CancelledError:
            # 关闭信号：静默退出（finally 负责 disconnect，避免 starlette 打印 ASGI traceback）
            pass
        except Exception as e:
            logger.error(f"客户端 {client_id} 连接异常: {e}")
        finally:
            await self.disconnect(client_id)

    def get_client_infos(self) -> List[ClientInfo]:
        """获取所有客户端信息"""
        return list(self._client_info.values())
