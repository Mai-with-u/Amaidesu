"""MCP 测试共享替身：不发网络请求的客户端、工具/结果鸭子类型与最小配置占位。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
