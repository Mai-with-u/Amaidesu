"""McpToolProvider.connect / disconnect / supports_reconnect 单测。

mock McpClient（不发真实网络请求）——验证：
- 已连接短路 True，未连接时调 McpClient.connect 并透传 bool
- disconnect 委托 McpClient.close（幂等）
- supports_reconnect 因覆写 connect → True
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


from src.modules.mcp.client import McpClient
from src.modules.mcp.provider import McpToolProvider


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
        self.connect_calls = 0
        self.close_calls = 0
        # 注入下一次 connect() 的返回值；None 表示用 self._connected
        self._next_connect_result: Optional[bool] = None

    def set_tools(self, tools: List[FakeTool]) -> None:
        self._tools = tools

    async def connect(self, timeout_seconds: Optional[float] = None) -> bool:
        self.connect_calls += 1
        if self._next_connect_result is not None:
            result = self._next_connect_result
            self._next_connect_result = None
            self._connected = result
            return result
        return self._connected

    async def list_tools(self) -> List[Any]:
        return list(self._tools) if self._connected else []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if not self._connected:
            return None
        return FakeResult(content=[FakeContent(f"ok:{tool_name}")], structured={"raw": tool_name})

    async def close(self) -> None:
        self.close_calls += 1
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected


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


def _provider() -> tuple[FakeMcpClient, McpToolProvider]:
    client = FakeMcpClient(name="serverA", config=AnyConfig())
    prov = McpToolProvider(client=client, server_name="serverA")
    return client, prov


# =============================================================================
# supports_reconnect
# =============================================================================


def test_mcp_provider_supports_reconnect() -> None:
    """McpToolProvider 覆写 connect → supports_reconnect=True。"""
    _, prov = _provider()
    assert prov.supports_reconnect is True


# =============================================================================
# connect
# =============================================================================


async def test_mcp_connect_short_circuit_when_already_connected() -> None:
    """已连接短路：直接返回 True，不调底层 connect。"""
    client, prov = _provider()
    assert client.connected is True
    assert await prov.connect() is True
    assert client.connect_calls == 0, "已连接短路不应穿透到 McpClient.connect"


async def test_mcp_connect_delegates_to_client_when_disconnected() -> None:
    """未连接 → 委托 McpClient.connect，透传 bool。"""
    client, prov = _provider()
    client._connected = False
    client.connect_calls = 0
    # 注入下一次 connect() 返回 True
    client._next_connect_result = True
    assert await prov.connect() is True
    assert client.connect_calls == 1


async def test_mcp_connect_returns_false_on_failure() -> None:
    """底层 connect 返回 False → prov.connect 返回 False。"""
    client, prov = _provider()
    client._connected = False
    client.connect_calls = 0
    client._next_connect_result = False  # 模拟底层失败
    assert await prov.connect() is False
    assert client.connect_calls == 1


# =============================================================================
# disconnect
# =============================================================================


async def test_mcp_disconnect_delegates_to_client_close() -> None:
    """disconnect 委托 McpClient.close，返回 True 表达动作完成。"""
    client, prov = _provider()
    assert client.close_calls == 0
    assert await prov.disconnect() is True
    assert client.close_calls == 1
    assert client._connected is False


async def test_mcp_disconnect_is_idempotent() -> None:
    """disconnect 幂等：未连接时也调用 close（底层 close 自身幂等）。"""
    client, prov = _provider()
    await prov.disconnect()
    # 二次调用不应抛
    assert await prov.disconnect() is True
    assert client.close_calls == 2


# =============================================================================
# reconnect 默认组合（不覆写 → disconnect + connect）
# =============================================================================


async def test_mcp_reconnect_default_compose() -> None:
    """reconnect 默认组合：先 disconnect 后 connect，透传 connect bool。"""
    client, prov = _provider()
    client.close_calls = 0
    client.connect_calls = 0
    # disconnect 会把 client._connected 置 False；connect 注入一次 True
    client._next_connect_result = True
    assert await prov.reconnect() is True
    assert client.close_calls == 1
    assert client.connect_calls == 1
