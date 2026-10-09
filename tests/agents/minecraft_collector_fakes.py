"""MaiCraft 事件流采集器测试共用的替身：MCP 客户端、事件总线与 events 的一页回复。

身体事件采集器与聊天采集器都长轮询 MaiCraft v1 的 events，连接与回复形状一样，替身放在一处。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest


class FakeEventBus:
    """捕获 emit 的桩（替代真实 EventBus）。"""

    def __init__(self) -> None:
        self.events: List[tuple] = []

    async def emit(self, event_name: str, payload: Any, **kwargs: Any) -> None:
        self.events.append((event_name, payload))


class FakeMcpClient:
    """MCP 客户端替身：连接、工具清单与 events 的回复都可控。"""

    instances: List["FakeMcpClient"] = []
    tool_names = ("observe", "lookup", "execute", "task", "events")

    def __init__(self, name: str, config: Any) -> None:
        self.name = name
        self.config = config
        self.connected = True
        self.closed = False
        self.pages: List[Dict[str, Any]] = []
        self.read_error: Optional[Exception] = None
        self.read_calls: List[Dict[str, Any]] = []
        FakeMcpClient.instances.append(self)

    async def connect(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.closed = True
        self.connected = False

    async def list_tools(self) -> List[Any]:
        return [FakeTool(name) for name in self.tool_names]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        self.read_calls.append({"name": name, "arguments": dict(arguments)})
        if self.read_error is not None:
            raise self.read_error
        page = self.pages.pop(0) if self.pages else events_page([], cursor=0)
        return FakeCallResult({"ok": True, "data": page})


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"desc {name}"
        self.inputSchema = {"type": "object"}


class FakeCallResult:
    def __init__(self, structured: Dict[str, Any]) -> None:
        self.is_error = False
        self.content = []
        self.structured_content = structured


def events_page(
    events: List[Dict[str, Any]],
    *,
    cursor: int,
    stream_id: str = "stream-A",
    status: str = "valid",
    has_more: bool = False,
) -> Dict[str, Any]:
    """一页 events 的 data（形状与 MaiCraft v1 的 events 一致）。"""
    return {"stream_id": stream_id, "cursor": cursor, "has_more": has_more, "cursor_status": status, "events": events}


def patch_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """把采集器用到的 MCP 客户端换成替身（采集器在 _ensure_ready 内函数级 import）；provider 保持真实实现。"""
    import src.modules.mcp.client as client_module
    import src.modules.mcp.provider as provider_module

    FakeMcpClient.instances = []
    monkeypatch.setattr(client_module, "McpClient", FakeMcpClient)
    monkeypatch.setattr(provider_module, "McpClient", FakeMcpClient)
