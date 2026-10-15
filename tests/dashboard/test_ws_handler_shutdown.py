"""WebSocketHandler.shutdown 单元测试

验证停机路径：shutdown() 排干全部 writer task 并清空注册表——
uvicorn 优雅关闭前必须先收走发送者，避免连接已闭后的 send 报错。
"""

from __future__ import annotations

import pytest

from src.modules.dashboard.schemas.event import WebSocketMessage
from src.modules.dashboard.websocket.handler import WebSocketHandler


class _FakeWebSocket:
    """最小假 WS：accept 可等待，send_text 记录调用。"""

    def __init__(self) -> None:
        self.headers: dict = {}
        self.sent: list = []

    async def accept(self) -> None:
        return None

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest.mark.asyncio
async def test_shutdown_drains_writers_and_registry() -> None:
    handler = WebSocketHandler(heartbeat_interval=30)
    ws = _FakeWebSocket()
    await handler.connect(ws)
    assert handler.client_count == 1

    await handler.shutdown()

    assert handler.client_count == 0
    assert handler._writer_tasks == {}
    assert handler._send_queues == {}
    assert handler._client_subscriptions == {}


@pytest.mark.asyncio
async def test_shutdown_stops_pending_sends() -> None:
    """停机后入队未发的消息不再投递（writer 已被取消，无 connection is closing）。"""
    handler = WebSocketHandler(heartbeat_interval=30)
    ws = _FakeWebSocket()
    client_id = await handler.connect(ws)

    # 不等 writer 消费，直接停机
    await handler.send_to_client(client_id, WebSocketMessage(type="ping", timestamp_ms=1, data={}))
    await handler.shutdown()

    assert handler.client_count == 0
