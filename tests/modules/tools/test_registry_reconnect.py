"""
ToolRegistry.reconnect_provider / provider_supports_reconnect 单测。

覆盖四条核心路径：
- 未注册 provider_id → ok=False, error 含"未注册 Provider"
- 已注册但不支持重连 → ok=False, error 含"不支持手动重连"
- 重连失败 → ok=False, provider_id 透传
- 重连成功 → ok=True，recovered 与 still_tripped 准确切片

附属覆盖：provider_supports_reconnect 三分支（BaseToolProvider 支持 / 不支持 / 非 BaseToolProvider）。
"""

from __future__ import annotations

import pytest

from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


class _ConnectableProvider(BaseToolProvider):
    """覆写 connect 的 Provider，重连由 caller 控制成败。"""

    def __init__(self, connect_ok: bool = True) -> None:
        self.connect_ok = connect_ok
        self.connect_calls = 0
        self.disconnect_calls = 0

    @property
    def name(self) -> str:
        return "conn"

    def list_tools(self):
        return [ToolSpec(name="a", description="a", kind="sync", provider="conn")]

    async def invoke(self, invocation: ToolInvocation):
        from src.modules.tools.models import ToolExecutionResult

        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def health_check(self) -> bool:
        return True

    async def connect(self) -> bool:
        self.connect_calls += 1
        return self.connect_ok

    async def disconnect(self) -> bool:
        self.disconnect_calls += 1
        return True


class _NoConnectProvider(BaseToolProvider):
    """不覆写 connect 的 Provider → supports_reconnect=False。"""

    @property
    def name(self) -> str:
        return "noconn"

    def list_tools(self):
        return [ToolSpec(name="x", description="x", kind="sync", provider="noconn")]

    async def invoke(self, invocation: ToolInvocation):
        from src.modules.tools.models import ToolExecutionResult

        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def health_check(self) -> bool:
        return True


class _NonBaseProvider:
    """非 BaseToolProvider 子类——纯 duck-typed 注册到 registry。"""

    @property
    def name(self) -> str:
        return "legacy"

    category = ""

    def list_tools(self):
        return [ToolSpec(name="legacy_t", description="lt", kind="sync", provider="legacy")]

    async def invoke(self, invocation: ToolInvocation):
        from src.modules.tools.models import ToolExecutionResult

        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)


# =============================================================================
# provider_supports_reconnect
# =============================================================================


def test_provider_supports_reconnect_true_for_connectable(registry: ToolRegistry) -> None:
    """覆写 connect 的 BaseToolProvider → provider_supports_reconnect=True。"""
    provider = _ConnectableProvider()
    registry.register_provider(provider)
    assert registry.provider_supports_reconnect("conn_a") is True


def test_provider_supports_reconnect_false_for_no_connect(registry: ToolRegistry) -> None:
    """未覆写 connect 的 BaseToolProvider → provider_supports_reconnect=False。"""
    provider = _NoConnectProvider()
    registry.register_provider(provider)
    assert registry.provider_supports_reconnect("x") is False


def test_provider_supports_reconnect_false_for_non_base_provider(registry: ToolRegistry) -> None:
    """非 BaseToolProvider 子类 → provider_supports_reconnect=False（兼容性兜底）。"""
    provider = _NonBaseProvider()
    registry.register_provider(provider)  # type: ignore[arg-type]
    assert registry.provider_supports_reconnect("legacy_t") is False


def test_provider_supports_reconnect_false_for_unknown_tool(registry: ToolRegistry) -> None:
    """未注册工具名 → provider_supports_reconnect=False。"""
    assert registry.provider_supports_reconnect("ghost") is False


# =============================================================================
# reconnect_provider 四路径
# =============================================================================


async def test_reconnect_provider_unknown_returns_error(registry: ToolRegistry) -> None:
    """未注册 provider_id → 返回未注册错误。"""
    report = await registry.reconnect_provider("nonexistent")
    assert report["ok"] is False
    assert "未注册 Provider" in report["error"]
    assert "nonexistent" in report["error"]


async def test_reconnect_provider_unsupported_returns_error(registry: ToolRegistry) -> None:
    """已注册但不支持重连 → 返回不支持错误（不触发重连动作）。"""
    provider = _NoConnectProvider()
    registry.register_provider(provider)
    report = await registry.reconnect_provider("noconn")
    assert report["ok"] is False
    assert "不支持手动重连" in report["error"]
    assert "noconn" in report["error"]


async def test_reconnect_provider_non_base_provider_unsupported(registry: ToolRegistry) -> None:
    """非 BaseToolProvider 子类 → 同样报不支持（与 supports_reconnect 单点判定一致）。"""
    provider = _NonBaseProvider()
    registry.register_provider(provider)  # type: ignore[arg-type]
    report = await registry.reconnect_provider("legacy")
    assert report["ok"] is False
    assert "不支持手动重连" in report["error"]


async def test_reconnect_provider_failure_returns_error_with_id(registry: ToolRegistry) -> None:
    """重连返回 False → ok=False，provider_id 透传。"""
    provider = _ConnectableProvider(connect_ok=False)
    registry.register_provider(provider)
    report = await registry.reconnect_provider("conn")
    assert report["ok"] is False
    assert "重连失败" in report["error"]
    assert report["provider_id"] == "conn"
    # 至少调了一次 connect（默认 reconnect 组合）
    assert provider.connect_calls == 1


async def test_reconnect_provider_success_recover_tripped_tools(registry: ToolRegistry) -> None:
    """重连成功 → 探活归属工具，通过且熔断的复位；探活未通过的留在 still_tripped。"""
    provider = _ConnectableProvider(connect_ok=True)
    registry.register_provider(provider)

    # 人为制造一个熔断中 + 一个探活失败的归属工具
    registry._health["conn_a"] = _tripped()
    # 通过伪造另一个工具名，但归属同一 provider：测试用 helper
    registry._tools["conn_a_fail"] = (
        ToolSpec(name="conn_a_fail", description="f", kind="sync", provider="conn"),
        provider.invoke,
    )
    registry._tool_owner["conn_a_fail"] = provider
    registry._health["conn_a_fail"] = _tripped()

    # 把 conn_a 的 health_check 改成 True；conn_a_fail 的 health_check 改成 False
    # 通过 monkey-patch provider.health_check
    health_results = iter([True, False])

    async def _toggle_health() -> bool:
        return next(health_results)

    provider.health_check = _toggle_health  # type: ignore[method-assign]

    report = await registry.reconnect_provider("conn")
    assert report["ok"] is True
    assert report["provider_id"] == "conn"
    assert report["recovered"] == ["conn_a"]
    assert report["still_tripped"] == ["conn_a_fail"]
    # 熔断器状态也确认翻转
    assert not registry.is_tripped("conn_a")
    assert registry.is_tripped("conn_a_fail")


async def test_reconnect_provider_success_with_no_tripped_tools(registry: ToolRegistry) -> None:
    """重连成功但没有归属工具处于熔断中 → recovered 与 still_tripped 均空。"""
    provider = _ConnectableProvider(connect_ok=True)
    registry.register_provider(provider)
    report = await registry.reconnect_provider("conn")
    assert report["ok"] is True
    assert report["recovered"] == []
    assert report["still_tripped"] == []


async def test_reconnect_provider_connect_exception_is_caught(registry: ToolRegistry) -> None:
    """provider.reconnect 抛出 → 兜底为 ok=False + error 带异常信息，不上抛。"""

    class _BoomProvider(_ConnectableProvider):
        async def connect(self) -> bool:
            raise RuntimeError("kaboom")

    provider = _BoomProvider(connect_ok=False)
    registry.register_provider(provider)
    report = await registry.reconnect_provider("conn")
    assert report["ok"] is False
    assert "kaboom" in report["error"]
    assert report["provider_id"] == "conn"


def _tripped():
    """构造一个熔断中的 _ToolHealth（测试用 helper）。"""
    from src.modules.tools.registry import _ToolHealth

    return _ToolHealth(consecutive_failures=3, tripped=True, tripped_at_ms=123, last_error="boom")
