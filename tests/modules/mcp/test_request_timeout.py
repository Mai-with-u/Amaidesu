"""MCP 不回包时必须有界返回，并保留远端操作结果未知这一事实。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.modules.mcp.client import McpClient
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools import ToolInvocation, ToolRegistry, ToolSpec


class SilentEndpoint:
    """记录请求已收到，随后永不回包，用来复现通道静默而进程仍活着的情况。"""

    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls += 1
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


async def test_silent_tool_returns_unknown_outcome_without_automatic_replay(loguru_capture: Any) -> None:
    """远端可能已经执行操作；本地超时只结束等待，不能声称未执行或自动再发一次。"""
    endpoint = SilentEndpoint()
    client = McpClient("reference", SimpleNamespace(request_timeout_ms=25, timeout_seconds=1))
    client._client = endpoint
    client._connected = True
    provider = McpToolProvider(client=client, server_name="reference")
    provider._specs = [ToolSpec(name="operate", description="", provider="reference")]
    registry = ToolRegistry()
    registry.register_provider(provider)
    # 外层仅保护测试进程；真正的错误结果必须由生产通道的请求期限产生。
    result = await asyncio.wait_for(registry.invoke(ToolInvocation(tool_name="reference_operate")), timeout=1)
    assert not result.success
    assert result.failure_kind == "execution"
    error = result.structured_content["error"]
    assert error["code"] == "mcp_request_timeout" and error["outcome_known"] is False
    assert error["timeout_ms"] == 25 and error["request_id"]
    # 同一关联编号同时出现开始与超时结束，排查时才能定位没有返回的那次请求。
    messages = [entry["message"] for entry in loguru_capture.records if error["request_id"] in entry["message"]]
    assert any("开始" in message for message in messages)
    assert any("结束 status=timeout" in message for message in messages)
    assert endpoint.calls == 1 and endpoint.cancelled
    assert not client.connected
    assert client._client is endpoint  # 下一次重连仍需释放旧会话，不能丢失清理入口。


async def test_caller_cancellation_remains_cancellation() -> None:
    """用户主动中断不能变成可重试超时，也不能继续占用等待协程。"""
    endpoint = SilentEndpoint()
    client = McpClient("reference", SimpleNamespace(request_timeout_ms=1000, timeout_seconds=1))
    client._client = endpoint
    client._connected = True
    call = asyncio.create_task(client.call_tool("read", {}))
    await asyncio.sleep(0)
    call.cancel()
    try:
        await call
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("caller cancellation must propagate")
    assert endpoint.calls == 1 and endpoint.cancelled


@pytest.mark.parametrize("method,arguments,expected", [
    ("list_tools", (), []), ("list_resources", (), []), ("read_resource", ("resource://sample",), None),
])
async def test_discovery_and_resource_reads_are_bounded(method: str, arguments: tuple, expected: Any) -> None:
    """读取目录或订阅后的资源快照也可能不回包，不能只保护模型直接调用的工具。"""
    async def silent(*args: Any) -> Any:
        await asyncio.Event().wait()

    client = McpClient("reference", SimpleNamespace(request_timeout_ms=25, timeout_seconds=1))
    client._client = SimpleNamespace(**{method: silent})
    client._connected = True
    result = await asyncio.wait_for(getattr(client, method)(*arguments), timeout=1)
    assert result == expected and not client.connected
