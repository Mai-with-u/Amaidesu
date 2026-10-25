"""ConnectionToolProvider 连接态中间层：降级清单、mark_* 触发回调、回调异常隔离、_connected_tools 分派"""

from __future__ import annotations

from typing import Callable, List, Optional

import pytest

from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import ConnectionToolProvider, wire_connection_tools


class _FakeRegistry:
    """记录 refresh 调用的假注册表。"""

    def __init__(self) -> None:
        self.refreshed: List[object] = []

    def refresh_provider_tools(self, provider: object) -> dict:
        self.refreshed.append(provider)
        return {}


def _make_spec(provider_name: str, tool_name: str) -> ToolSpec:
    return ToolSpec(name=tool_name, description=tool_name, kind="sync", provider=provider_name)


class _StubProvider(ConnectionToolProvider):
    """最小子类：注入回调槽，清单由外部可控。"""

    def __init__(self, name: str = "stub") -> None:
        self._name = name
        self.on_connection_changed: Optional[Callable[[bool], None]] = None
        self.tool_names: List[str] = ["t1", "t2"]

    @property
    def name(self) -> str:
        return self._name

    def _connected_tools(self) -> List[ToolSpec]:
        return [_make_spec(self._name, n) for n in self.tool_names]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)


def test_initial_disconnected_returns_empty() -> None:
    """初始未连接：list_tools 返回空清单（降级登记 0 工具）。"""
    provider = _StubProvider()
    assert provider.list_tools() == []
    assert provider.on_connection_changed is None  # 默认未接线


def test_mark_connected_fires_callback_and_lists_tools() -> None:
    """mark_connected：回调收到 True，清单切到子类全量。"""
    provider = _StubProvider()
    seen: List[bool] = []
    provider.on_connection_changed = seen.append
    provider.mark_connected()
    assert seen == [True]
    assert [s.name for s in provider.list_tools()] == ["t1", "t2"]


def test_mark_disconnected_fires_callback_and_drops_tools() -> None:
    """mark_disconnected：回调收到 False，清单回到空。"""
    provider = _StubProvider()
    seen: List[bool] = []
    provider.on_connection_changed = seen.append
    provider.mark_connected()
    provider.mark_disconnected()
    assert seen == [True, False]
    assert provider.list_tools() == []


def test_callback_exception_swallowed() -> None:
    """回调抛异常被吞掉放行：连接态标志照常迁移，异常不外抛。"""
    provider = _StubProvider()

    def _boom(connected: bool) -> None:
        raise RuntimeError("换血失败")

    provider.on_connection_changed = _boom
    provider.mark_connected()
    assert [s.name for s in provider.list_tools()] == ["t1", "t2"]
    provider.mark_disconnected()
    assert provider.list_tools() == []


def test_connected_tools_dispatched_on_demand() -> None:
    """清单每次调用都分派 _connected_tools：子类动态改清单即时生效。"""
    provider = _StubProvider()
    provider.mark_connected()
    assert len(provider.list_tools()) == 2
    provider.tool_names.append("t3")
    assert [s.name for s in provider.list_tools()] == ["t1", "t2", "t3"]


def test_subclass_without_connected_tools_raises() -> None:
    """未实现 _connected_tools 的子类：连接后调用即抛 NotImplementedError。"""
    provider = _StubProvider()
    provider._connected_tools = lambda: (_ for _ in ()).throw(NotImplementedError("missing"))  # type: ignore[method-assign]
    provider.mark_connected()
    with pytest.raises(NotImplementedError):
        provider.list_tools()


def test_wire_connection_tools_refreshes_registry() -> None:
    """wire_connection_tools：接线后 mark_* 驱动 registry.refresh_provider_tools。"""
    provider = _StubProvider()
    registry = _FakeRegistry()
    wire_connection_tools(provider, registry)  # type: ignore[arg-type]
    provider.mark_connected()
    provider.mark_disconnected()
    assert registry.refreshed == [provider, provider]
