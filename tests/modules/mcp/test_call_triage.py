"""call_tool 异常分诊：业务错误（ToolError）不断连，传输故障标记断连

背景：server 正常应答业务错误（参数错/状态冲突）时 fastmcp 抛 ToolError，
round-trip 完整、连接无恙——旧实现一律标记断连，导致健康连接被反复销毁重建。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.modules.mcp.client import McpClient
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools.models import ToolExecutionResult, ToolInvocation


class AnyConfig:
    """最简配置占位（client 构造仅需存在，与 test_provider.py 同款）。"""


class _InnerRaiser:
    """内层 fastmcp client 替身：call_tool 按配置抛指定异常。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        raise self._exc


class _TriagedClient(McpClient):
    """测试替身：已连接，内层 client 抛错——走真实 McpClient.call_tool 分诊逻辑。"""

    def __init__(self, exc: Exception, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._connected = True
        self._client = _InnerRaiser(exc)


@pytest.mark.asyncio
async def test_tool_error_keeps_connection_and_reraises() -> None:
    """业务错误：连接保持（不断连），异常上抛由 Provider 转述。"""
    from fastmcp.exceptions import ToolError

    client = _TriagedClient(
        ToolError('{"code":"invalid_arguments","message":"need label"}'), name="triage", config=AnyConfig()
    )
    with pytest.raises(ToolError):
        await client.call_tool("t", {})
    assert client._connected is True
    assert client._client is not None


@pytest.mark.asyncio
async def test_transport_error_marks_disconnected_and_returns_none() -> None:
    """传输故障：标记断连 + 返回 None（维持既有惰性重连语义）。"""
    client = _TriagedClient(ConnectionError("connection reset"), name="triage", config=AnyConfig())
    assert await client.call_tool("t", {}) is None
    assert client._connected is False
    assert client._client is None


@pytest.mark.asyncio
async def test_provider_invoke_converts_tool_error_to_failure_result() -> None:
    """全链路：真实分诊保持连接 → Provider 转失败结果（错误文本透传），永不外抛。"""
    from fastmcp.exceptions import ToolError

    provider = McpToolProvider(
        client=_TriagedClient(
            ToolError('{"error":{"code":"invalid_arguments","message":"need label"}}'),
            name="triage",
            config=AnyConfig(),
        ),
        server_name="maicraft",
    )
    provider._specs = []
    provider._name_map = {"maicraft_speak": "speak"}

    result = await provider.invoke(ToolInvocation(tool_name="maicraft_speak", arguments={}))
    assert isinstance(result, ToolExecutionResult)
    assert result.success is False
    assert "MCP 业务错误" in result.error_message
    assert "invalid_arguments" in result.error_message
    assert provider._client._connected is True
