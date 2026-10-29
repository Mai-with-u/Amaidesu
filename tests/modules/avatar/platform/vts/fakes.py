"""VTS 测试替身：按请求类型自动回复的假 WebSocket、只记录发送的假客户端"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional


def _api_error(error_id: int, message: str) -> Dict[str, Any]:
    return {"__error__": (error_id, message)}


class FakeVtsSocket:
    """假 VTS 插件接口连接：收到请求按 ``messageType`` 查回复表自动回复。

    ``hold = True`` 时回复先扣下，:meth:`release` 按给定顺序放出（模拟 VTS 乱序回复）；
    :meth:`drop` 模拟 VTS 那头断开。回复表的值可以是函数（入参为请求 data），返回
    :func:`api_error` 的结果时回 APIError。
    """

    def __init__(self, replies: Optional[Dict[str, Any]] = None) -> None:
        self.replies: Dict[str, Any] = {
            "AuthenticationRequest": {"authenticated": True},
            "AuthenticationTokenRequest": {"authenticationToken": "new-token"},
            "InjectParameterDataRequest": {},
            **(replies or {}),
        }
        self.sent: List[Dict[str, Any]] = []
        self.hold = False
        self.held: List[str] = []
        self.closed = False
        self._inbox: asyncio.Queue = asyncio.Queue()

    def sent_types(self) -> List[str]:
        return [message["messageType"] for message in self.sent]

    def _reply(self, message: Dict[str, Any]) -> str:
        message_type = message["messageType"]
        reply: Any = self.replies.get(message_type)
        if callable(reply):
            reply = reply(message.get("data") or {})
        if reply is None:
            reply = _api_error(1, f"假 VTS 不认识 {message_type}")
        if "__error__" in reply:
            error_id, text = reply["__error__"]
            body = {"messageType": "APIError", "data": {"errorID": error_id, "message": text}}
        else:
            body = {"messageType": message_type.replace("Request", "Response"), "data": reply}
        body["requestID"] = message["requestID"]
        return json.dumps(body)

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        reply = self._reply(message)
        if self.hold:
            self.held.append(reply)
        else:
            await self._inbox.put(reply)

    async def release(self, order: List[int]) -> None:
        for index in order:
            await self._inbox.put(self.held[index])
        self.held.clear()

    async def drop(self) -> None:
        await self._inbox.put(None)

    async def close(self) -> None:
        self.closed = True
        await self._inbox.put(None)

    def __aiter__(self) -> "FakeVtsSocket":
        return self

    async def __anext__(self) -> str:
        item = await self._inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item


def api_error(error_id: int, message: str) -> Dict[str, Any]:
    """回复表里表示"这条请求回 APIError"的值。"""
    return _api_error(error_id, message)


def socket_connector(socket: FakeVtsSocket) -> Callable[[str], Any]:
    """返回一个连接函数：每次连接都交出同一个假连接，并记下连接地址。"""

    async def connect(url: str) -> FakeVtsSocket:
        socket.url = url  # type: ignore[attr-defined]
        return socket

    return connect


class RecordingVtsClient:
    """VTS 客户端替身（驱动测试用）：只记录 ``send`` 发出的消息。"""

    def __init__(self, *, is_open: bool = True) -> None:
        self.is_open = is_open
        self.sent: List[tuple[str, Dict[str, Any]]] = []

    async def send(self, message_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        self.sent.append((message_type, data or {}))
