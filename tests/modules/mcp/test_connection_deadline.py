"""握手、旧连接退出和并发重连都必须受本机连接期限约束。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.modules.mcp.client import McpClient


class Session:
    """只替代 SDK 会话的建立和退出，保留生产通道的生命周期控制。"""

    def __init__(self, *, stuck_enter: bool = False, stuck_exit: bool = False) -> None:
        self.stuck_enter = stuck_enter
        self.stuck_exit = stuck_exit
        self.exits = 0

    async def __aenter__(self) -> Session:
        if self.stuck_enter:
            await asyncio.Event().wait()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.exits += 1
        if self.stuck_exit:
            await asyncio.Event().wait()


def make_client(monkeypatch: pytest.MonkeyPatch, factory: Any) -> McpClient:
    """注入会话工厂，不建立网络连接。"""
    monkeypatch.setattr("fastmcp.Client", factory)
    client = McpClient("reference", SimpleNamespace(timeout_seconds=0.025, request_timeout_ms=100, transport="http"))
    monkeypatch.setattr(client, "_build_transport", lambda: object())
    monkeypatch.setattr(client, "_build_message_handler", lambda subscriptions: None)
    return client


async def test_handshake_honors_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """握手一直不返回时，连接配置必须生效，不能拖住首次工具调用。"""
    client = make_client(monkeypatch, lambda *args, **kwargs: Session(stuck_enter=True))
    assert await asyncio.wait_for(client.connect(), timeout=1) is False
    assert not client.connected
    await client.close()


async def test_close_honors_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧会话退出卡住时，也要释放本地引用并结束关闭请求。"""
    client = make_client(monkeypatch, lambda *args, **kwargs: Session())
    old = Session(stuck_exit=True)
    client._client = old
    client._connected = True
    await asyncio.wait_for(client.close(), timeout=1)
    assert not client.connected and client._client is None
    assert old.exits == 1


async def test_concurrent_connects_share_one_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """多个消费者同时发现断线时只创建一个新会话，避免相互关闭刚建立的连接。"""
    made: list[Session] = []

    def create(*args: Any, **kwargs: Any) -> Session:
        session = Session()
        made.append(session)
        return session

    client = make_client(monkeypatch, create)
    try:
        assert await asyncio.gather(client.connect(), client.connect()) == [True, True]
        assert len(made) == 1 and made[0].exits == 0
    finally:
        await client.close()


async def test_real_sdk_recovers_after_a_timed_out_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 FastMCP 会话超时后释放并重建，后续调用可成功，先前操作没有被重放。"""
    # 在这项集成测试中才加载可选 SDK，服务在进程内运行，不连接游戏或调用模型。
    from fastmcp import FastMCP

    service = FastMCP("deadline-test")
    calls: list[bool] = []

    @service.tool()
    async def inspect(wait: bool) -> str:
        calls.append(wait)
        if wait:
            await asyncio.Event().wait()
        return "available"

    client = McpClient("reference", SimpleNamespace(timeout_seconds=1, request_timeout_ms=50, transport="http"))
    monkeypatch.setattr(client, "_build_transport", lambda: service)
    try:
        assert await client.connect()
        with pytest.raises(TimeoutError, match="远端结果未知"):
            await asyncio.wait_for(client.call_tool("inspect", {"wait": True}), timeout=2)
        result = await asyncio.wait_for(client.call_tool("inspect", {"wait": False}), timeout=2)
        assert result.content[0].text == "available"
        assert calls == [True, False] and client.connected
    finally:
        await client.close()
