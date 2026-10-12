"""
BaseToolProvider / ToolProvider 连接动作契约单测。

覆盖：
- 默认实现：connect/disconnect 返回 False，reconnect 默认组合（disconnect + connect）
- supports_reconnect 单点判定（type(self).connect is not BaseToolProvider.connect）
- _SpecImplProvider 不支持重连（与"无状态 Provider 无重连按钮"前端契约一致）
- 自定义 Provider 覆写 connect 即可自动启用重连支持
"""

from __future__ import annotations


from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider, make_provider_from_specs


class _MinimalProvider(BaseToolProvider):
    """最小 BaseToolProvider 子类（不覆写 connect）。"""

    @property
    def name(self) -> str:
        return "minimal"

    def list_tools(self):
        return [ToolSpec(name="noop", description="noop", kind="sync", provider="minimal")]

    async def invoke(self, invocation: ToolInvocation):
        from src.modules.tools.models import ToolExecutionResult

        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)


class _ConnectableProvider(BaseToolProvider):
    """覆写 connect 的 Provider：自动启用 supports_reconnect。"""

    def __init__(self) -> None:
        self.connect_calls = 0
        self.disconnect_calls = 0

    @property
    def name(self) -> str:
        return "connectable"

    def list_tools(self):
        return [ToolSpec(name="x", description="x", kind="sync", provider="connectable")]

    async def invoke(self, invocation: ToolInvocation):
        from src.modules.tools.models import ToolExecutionResult

        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def connect(self) -> bool:
        self.connect_calls += 1
        return True

    async def disconnect(self) -> bool:
        self.disconnect_calls += 1
        return True


# =============================================================================
# 默认实现
# =============================================================================


async def test_base_provider_connect_default_returns_false() -> None:
    """BaseToolProvider.connect 默认实现返回 False（不支持建连）。"""
    p = _MinimalProvider()
    assert await p.connect() is False


async def test_base_provider_disconnect_default_returns_false() -> None:
    """BaseToolProvider.disconnect 默认实现返回 False（不支持断开）。"""
    p = _MinimalProvider()
    assert await p.disconnect() is False


async def test_base_provider_reconnect_default_composes_disconnect_and_connect() -> None:
    """reconnect 默认组合：先调 disconnect 再调 connect，返回 connect 结果。"""
    p = _MinimalProvider()
    # 二者默认均 False → reconnect 整体返回 False
    assert await p.reconnect() is False


# =============================================================================
# supports_reconnect 单点判定
# =============================================================================


def test_base_provider_supports_reconnect_default_is_false() -> None:
    """未覆写 connect 的 BaseToolProvider 子类 → supports_reconnect=False。"""
    p = _MinimalProvider()
    assert p.supports_reconnect is False


def test_connectable_provider_supports_reconnect_is_true() -> None:
    """覆写 connect 的 Provider → supports_reconnect 自动为 True（单点判定）。"""
    p = _ConnectableProvider()
    assert p.supports_reconnect is True


def test_spec_impl_provider_does_not_support_reconnect() -> None:
    """make_provider_from_specs 工厂产出的 Provider 不支持重连（无 connect 覆写）。"""

    async def _echo(inv: ToolInvocation):
        from src.modules.tools.models import ToolExecutionResult

        return ToolExecutionResult(tool_name=inv.tool_name, success=True, content="ok")

    p = make_provider_from_specs(
        name="builtin_echo",
        spec_impl_pairs=[
            (ToolSpec(name="echo", description="echo", kind="sync", provider="builtin_echo"), _echo),
        ],
    )
    assert p.supports_reconnect is False
    # 默认实现路径上：connect/disconnect 均返回 False
    assert isinstance(p, BaseToolProvider)


# =============================================================================
# 覆写 connect 即触发重连默认组合
# =============================================================================


async def test_connectable_reconnect_uses_default_composition() -> None:
    """覆写 connect 但不覆写 reconnect → 仍走默认 disconnect+connect 组合。"""
    p = _ConnectableProvider()
    assert await p.reconnect() is True
    assert p.connect_calls == 1
    assert p.disconnect_calls == 1


class _ReconnectOverrideProvider(_ConnectableProvider):
    """整体覆写 reconnect（不走 disconnect+connect 默认组合）。"""

    def __init__(self) -> None:
        super().__init__()
        self.reconnect_calls = 0

    async def reconnect(self) -> bool:
        self.reconnect_calls += 1
        # 不调 disconnect；仅 connect
        return await self.connect()


async def test_reconnect_full_override_skips_default_composition() -> None:
    """整体覆写 reconnect → 不调 disconnect，直接走自定义逻辑。"""
    p = _ReconnectOverrideProvider()
    assert await p.reconnect() is True
    assert p.reconnect_calls == 1
    assert p.connect_calls == 1
    assert p.disconnect_calls == 0  # 不走默认组合
