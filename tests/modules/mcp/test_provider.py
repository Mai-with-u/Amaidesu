"""McpToolProvider 单测：缓存 specs + invoke 转发

mock McpClient（不发真实网络请求）——验证：
- setup() 预拉工具列表 → list_tools() 同步返回缓存
- invoke() 剥前缀转发、结果映射、未知工具失败、未连接降级
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from src.modules.mcp.client import McpClient
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools.models import ToolInvocation


class FakeTool:
    def __init__(self, name: str, description: str = "", input_schema: Any = None) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class FakeContent:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeResult:
    def __init__(self, content: Any = None, is_error: bool = False, structured: Any = None) -> None:
        self.content = content
        self.is_error = is_error
        self.structured_content = structured


class FakeMcpClient(McpClient):
    """测试替身：不发网络请求，记录调用。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._connected = True
        self._tools: List[FakeTool] = []
        self.called: List[Dict[str, Any]] = []
        self.closed = False

    def set_tools(self, tools: List[FakeTool]) -> None:
        self._tools = tools

    async def connect(self, timeout_seconds: Optional[float] = None) -> bool:
        return self._connected

    async def list_tools(self) -> List[Any]:
        return list(self._tools) if self._connected else []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if not self._connected:
            return None
        self.called.append({"name": tool_name, "arguments": arguments})
        return FakeResult(content=[FakeContent(f"ok:{tool_name}")], structured={"raw": tool_name})

    async def close(self) -> None:
        self.closed = True
        self._connected = False


def _provider(server_name: str = "serverA", prefix: Optional[str] = None) -> tuple[FakeMcpClient, McpToolProvider]:
    client = FakeMcpClient(name=server_name, config=AnyConfig())
    prov = McpToolProvider(client=client, server_name=server_name, prefix=prefix)
    return client, prov


class AnyConfig:
    transport = "http"
    url = "http://127.0.0.1:8766/mcp"
    headers = {}
    env = {}
    args = []
    command = None
    prefix = None
    enabled = True
    reconnect = True
    timeout_seconds = 30.0


class TestProvider:
    async def test_setup_caches_tools(self) -> None:
        client, prov = _provider()
        client.set_tools(
            [
                FakeTool("perceive", "观察"),
                FakeTool("execute", "执行", input_schema={"type": "object"}),
            ]
        )
        count = await prov.setup()
        assert count == 2
        specs = list(prov.list_tools())
        assert [s.name for s in specs] == ["serverA_perceive", "serverA_execute"]
        assert specs[0].provider == "serverA"
        assert specs[1].parameters_schema == {"type": "object"}

    async def test_setup_connection_failure_returns_zero(self) -> None:
        client, prov = _provider()
        client._connected = False
        count = await prov.setup()
        assert count == 0
        assert list(prov.list_tools()) == []

    async def test_invoke_strips_prefix_and_forwards(self) -> None:
        client, prov = _provider()
        client.set_tools([FakeTool("perceive", "观察")])
        await prov.setup()

        result = await prov.invoke(ToolInvocation(tool_name="serverA_perceive", arguments={"view": "situation"}))
        assert result.success is True
        assert client.called == [{"name": "perceive", "arguments": {"view": "situation"}}]
        assert result.structured_content == {"raw": "perceive"}

    async def test_invoke_unknown_tool_returns_failure(self) -> None:
        client, prov = _provider()
        client.set_tools([FakeTool("perceive", "观察")])
        await prov.setup()

        result = await prov.invoke(ToolInvocation(tool_name="serverA_nonexistent"))
        assert result.success is False
        assert "不属于" in result.error_message
        assert client.called == []

    async def test_invoke_before_setup_returns_failure(self) -> None:
        client, prov = _provider()
        # 未 setup：specs 为空 → 任何工具都被视为不属于本 Provider
        result = await prov.invoke(ToolInvocation(tool_name="serverA_perceive"))
        assert result.success is False

    async def test_invoke_call_failure_maps_to_error_result(self) -> None:
        client, prov = _provider()
        client.set_tools([FakeTool("perceive", "观察")])
        await prov.setup()
        client._connected = False  # 模拟断连 → call_tool 返回 None

        result = await prov.invoke(ToolInvocation(tool_name="serverA_perceive"))
        assert result.success is False
        assert "失败" in result.error_message

    async def test_close_delegates_to_client(self) -> None:
        client, prov = _provider()
        await prov.close()
        assert client.closed is True

    async def test_provider_name(self) -> None:
        _, prov = _provider()
        assert prov.name == "McpProvider:serverA"

    async def test_custom_prefix(self) -> None:
        client, prov = _provider(prefix="mc_")
        client.set_tools([FakeTool("perceive", "观察")])
        await prov.setup()
        assert list(prov.list_tools())[0].name == "mc_perceive"
