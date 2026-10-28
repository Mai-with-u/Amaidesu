"""MCP 测试共享替身：不发网络请求的客户端、工具/结果鸭子类型与最小配置占位。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from src.modules.mcp.client import McpClient


class FakeTool:
    """mcp.types.Tool 的鸭子类型。"""

    def __init__(self, name: str, description: str = "", input_schema: Any = None) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class FakeContent:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeResult:
    """CallToolResult 的鸭子类型。"""

    def __init__(self, content: Any = None, is_error: bool = False, structured: Any = None) -> None:
        self.content = content
        self.is_error = is_error
        self.structured_content = structured


class AnyConfig:
    """最小配置占位：McpClient 构造只要求它存在。"""

    transport = "http"
    url = "http://127.0.0.1:8766/mcp"
    headers: dict = {}
    env: dict = {}
    args: list = []
    command = None
    prefix = None
    enabled = True
    reconnect = True
    timeout_seconds = 30.0


class FakeSession:
    """记录订阅/退订请求的 session 替身。"""

    def __init__(self) -> None:
        self.subscribed: List[str] = []
        self.unsubscribed: List[str] = []
        self.fail_on_subscribe = False

    async def subscribe_resource(self, uri: Any) -> None:
        if self.fail_on_subscribe:
            raise ConnectionError("subscribe refused")
        self.subscribed.append(str(uri))

    async def unsubscribe_resource(self, uri: Any) -> None:
        self.unsubscribed.append(str(uri))


class FakeFastmcpClient:
    """fastmcp.Client 替身：接住 message_handler、暴露 session。

    call_tool 恒抛传输错误：供测试经公开的 ``call_tool`` 路径触发
    McpClient 的断连标记与重连，不再手撕内部连接状态。
    """

    def __init__(self, transport: Any, message_handler: Any = None, init_timeout: float | None = None) -> None:
        self.message_handler = message_handler
        self.session = FakeSession()
        self.read_results: List[Any] = [("content",)]
        self.list_results: List[Any] = []

    async def __aenter__(self) -> "FakeFastmcpClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        raise ConnectionError("transport broken")

    async def read_resource(self, uri: str) -> Any:
        return self.read_results

    async def list_resources(self) -> List[Any]:
        return self.list_results


class ScriptedFastmcpClient(FakeFastmcpClient):
    """call_tool 行为由测试挂载的客户端替身（经 McpClient 的 client_factory 接缝注入）。

    未挂载行为时与 FakeFastmcpClient 同：call_tool 恒抛传输错误。
    """

    def __init__(self, *args: Any, call_tool_handler: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._call_tool_handler = call_tool_handler

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        if self._call_tool_handler is None:
            raise ConnectionError("transport broken")
        return await self._call_tool_handler(name, arguments)


def scripted_client_factory(call_tool_handler: Any) -> Callable[..., Any]:
    """构造 McpClient 的 client_factory：call_tool 行为按测试需要挂载。"""

    def factory(transport: Any, message_handler: Any = None, init_timeout: float | None = None) -> Any:
        return ScriptedFastmcpClient(transport, call_tool_handler=call_tool_handler)

    return factory


class Updated:
    """ResourceUpdatedNotification 形状替身：handler 只用 params.uri。"""

    def __init__(self, uri: str) -> None:
        self.params = SimpleNamespace(uri=uri)


class Endpoint:
    """会话替身骨架：空进/出实现；需要的行为由测试按需挂载方法。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.exits = 0

    async def __aenter__(self) -> "Endpoint":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.exits += 1


def recording_client_factory(record: List[Any]) -> Callable[..., Any]:
    """构造 McpClient 的 client_factory：把创建的替身实例收进 record 供断言。

    测试据此从接缝侧拿到会话替身（session/message_handler 等），
    不再读取 McpClient 的私有属性。
    """

    def factory(transport: Any, message_handler: Any = None, init_timeout: float | None = None) -> Any:
        fake = FakeFastmcpClient(transport, message_handler=message_handler, init_timeout=init_timeout)
        record.append(fake)
        return fake

    return factory


class FakeMcpClient(McpClient):
    """记录调用的客户端替身；默认已连接。

    next_connect_result 非 None 时覆盖下一次 connect() 的结果（并同步连接态），用完即清。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._connected = True
        self._tools: List[FakeTool] = []
        self.called: List[Dict[str, Any]] = []
        self.connect_calls = 0
        self.close_calls = 0
        self.next_connect_result: Optional[bool] = None

    def set_tools(self, tools: List[FakeTool]) -> None:
        self._tools = tools

    def set_connected(self, value: bool) -> None:
        """直接置连接态（模拟断线/上线），不经 connect() 的结果协议。"""
        self._connected = value

    async def connect(self, timeout_seconds: Optional[float] = None) -> bool:
        self.connect_calls += 1
        if self.next_connect_result is not None:
            self._connected, self.next_connect_result = self.next_connect_result, None
        return self._connected

    async def list_tools(self) -> List[Any]:
        return list(self._tools) if self._connected else []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if not self._connected:
            return None
        self.called.append({"name": tool_name, "arguments": arguments})
        return FakeResult(content=[FakeContent(f"ok:{tool_name}")], structured={"raw": tool_name})

    async def close(self) -> None:
        self.close_calls += 1
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected
