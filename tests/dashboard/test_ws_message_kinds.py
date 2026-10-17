"""WS 消息 kind 标记契约测试。

事件日志页按 kind 分流：只有 kind="event" 进事件缓冲，"log"/"transport"
不进。本文件锁住发送侧标记：总线事件广播 = event（默认）、日志通道 =
log、握手帧（connected / subscribe_response / ping）= transport。
"""

from __future__ import annotations

import asyncio
import json

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


async def _flush_writer() -> None:
    """让出控制权使 writer task 排干发送队列（入队与实际发送解耦）。"""
    await asyncio.sleep(0.05)


def _kinds_of(ws: _FakeWebSocket) -> dict[str, str]:
    """已发消息按 type 归类 kind。"""
    result: dict[str, str] = {}
    for text in ws.sent:
        message = json.loads(text)
        result[message["type"]] = message.get("kind", "")
    return result


@pytest.mark.asyncio
async def test_handshake_frames_are_marked_transport() -> None:
    handler = WebSocketHandler(heartbeat_interval=30)
    ws = _FakeWebSocket()

    client_id = await handler.connect(ws)
    await handler._handle_subscribe(client_id, type("Req", (), {"action": "subscribe", "events": ["*"]})())
    await handler.send_heartbeat()
    await _flush_writer()
    await handler.shutdown()

    kinds = _kinds_of(ws)
    assert kinds["connected"] == "transport"
    assert kinds["subscribe_response"] == "transport"
    assert kinds["ping"] == "transport"


@pytest.mark.asyncio
async def test_bus_events_default_to_event_kind_and_log_channel_marks_log() -> None:
    handler = WebSocketHandler(heartbeat_interval=30)
    ws = _FakeWebSocket()
    client_id = await handler.connect(ws)
    handler._client_subscriptions[client_id].add("*")

    await handler.broadcast("planner.decision", {"id": "x"})
    await handler.broadcast("log.entry", {"message": "boom"}, kind="log")
    await _flush_writer()
    await handler.shutdown()

    kinds = _kinds_of(ws)
    assert kinds["planner.decision"] == "event"
    assert kinds["log.entry"] == "log"


def test_message_kind_documented_values_roundtrip() -> None:
    """kind 四值在 schema 上可往返（协议词汇表的守护）。"""
    for kind in ("event", "stream", "log", "transport"):
        message = WebSocketMessage(kind=kind, type="t", timestamp_ms=0, data={})
        assert message.kind == kind
