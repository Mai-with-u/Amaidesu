"""业务拒绝必须留给调用者纠正，不能让健康的 MCP 工具从名单消失。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.mcp.client import McpClient
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools import ToolInvocation, ToolRegistry, ToolSpec


@pytest.mark.parametrize("as_exception", [True, False])
async def test_business_rejections_keep_tool_available_and_emit_errors(as_exception: bool) -> None:
    """异常与结果两种 MCP 错误形式均不熔断，修正参数后能继续调用。"""
    # FastMCP 是可选依赖，测试只在需要模拟其业务异常时加载。
    from fastmcp.exceptions import ToolError

    class Endpoint:
        async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
            if arguments.get("valid"):
                return SimpleNamespace(is_error=False, content=[], structured_content={"value": 1})
            if as_exception:
                raise ToolError('{"error":{"code":"invalid_arguments","message":"choose a valid field"}}')
            return SimpleNamespace(
                is_error=True,
                content=[{"type": "text", "text": "choose a valid field"}],
                structured_content={"error": {"code": "invalid_arguments"}},
            )

    client = McpClient(name="reference", config=SimpleNamespace())
    client._connected = True
    client._client = Endpoint()
    provider = McpToolProvider(client=client, server_name="reference")
    provider._specs = [ToolSpec(name="read", description="", provider="reference")]
    bus = EventBus(enable_stats=False)
    events: list[ToolResultPayload] = []
    delivered = asyncio.Event()

    async def observe(name: str, payload: ToolResultPayload, source: str) -> None:
        events.append(payload)
        if len(events) == 5:
            delivered.set()

    bus.on("tool.result.#", observe, ToolResultPayload)
    registry = ToolRegistry(event_bus=bus, failure_threshold=3)
    registry.register_provider(provider)
    try:
        for _ in range(4):
            result = await registry.invoke(ToolInvocation(tool_name="reference_read"))
            assert not result.success
            assert "choose a valid field" in result.error_message
            assert not registry.is_tripped("reference_read")
        assert client.connected
        assert any(tool.full_name == "reference_read" for tool in registry.list_tools())
        result = await registry.invoke(ToolInvocation(tool_name="reference_read", arguments={"valid": True}))
        assert result.success
        # 错误回执继续广播，免熔断不能把请求失败伪装成成功。
        await asyncio.wait_for(delivered.wait(), timeout=1)
        assert [event.status for event in events] == ["error"] * 4 + ["success"]
    finally:
        await bus.cleanup()


async def test_transport_failures_still_trip_tool() -> None:
    """链路没有返回有效业务回执时，保留连续故障的熔断保护。"""
    class Endpoint:
        async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
            raise ConnectionError("connection reset")

    client = McpClient(name="reference", config=SimpleNamespace())
    provider = McpToolProvider(client=client, server_name="reference")
    provider._specs = [ToolSpec(name="read", description="", provider="reference")]
    registry = ToolRegistry(failure_threshold=3)
    registry.register_provider(provider)
    for _ in range(3):
        # 每次恢复连接后仍发生传输故障，避免把重连实现混入熔断回归。
        client._connected = True
        client._client = Endpoint()
        result = await registry.invoke(ToolInvocation(tool_name="reference_read"))
        assert not result.success
        assert not client.connected
    assert registry.is_tripped("reference_read")
