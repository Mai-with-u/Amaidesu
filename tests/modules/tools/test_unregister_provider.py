"""unregister_provider 一致移除契约测试。

覆盖：移除 provider 后 ``_providers`` / ``_tools`` / ``_tool_owner`` /
``_visible_to`` 四处映射一致消失（另连带熔断状态与分类记录）；
重复移除幂等不报错；未注册 provider 返回 0。
"""

from __future__ import annotations

from typing import Iterable

from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry


class _EchoProvider(BaseToolProvider):
    """测试用最小 Provider：两个工具，固定可见名单。"""

    category = "test"

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    def list_tools(self) -> Iterable[ToolSpec]:
        return [
            ToolSpec(name="alpha", description="a", provider=self._name),
            ToolSpec(name="beta", description="b", provider=self._name),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)


def _make_registry_with_provider() -> tuple[ToolRegistry, _EchoProvider]:
    registry = ToolRegistry()
    provider = _EchoProvider("echo")
    registry.register_provider(provider, visible_to={"echo_alpha": ["streamer"]})
    return registry, provider


def test_unregister_removes_all_internal_maps() -> None:
    """移除后 providers / tools / tool_owner / visible_to 四处一致消失。"""
    registry, provider = _make_registry_with_provider()
    assert registry.has("echo_alpha") and registry.has("echo_beta")

    removed = registry.unregister_provider(provider)

    assert removed == 2
    assert provider not in registry._providers
    assert not registry.has("echo_alpha") and not registry.has("echo_beta")
    assert "echo_alpha" not in registry._tool_owner
    assert "echo_alpha" not in registry._visible_to
    assert "echo" not in registry._categories
    # 可见性查询口径同步消失（unknown → 默认名单，不悬挂旧名单）
    assert registry.visible_to_of("echo_alpha") == ["streamer"]
    assert registry.provider_of_tool("echo_alpha") is None


def test_unregister_is_idempotent() -> None:
    """重复移除不报错，第二次返回 0；registry 状态不变。"""
    registry, provider = _make_registry_with_provider()
    assert registry.unregister_provider(provider) == 2
    assert registry.unregister_provider(provider) == 0
    assert registry.unregister_provider(provider) == 0
    assert len(registry) == 0


def test_unregister_keeps_other_providers() -> None:
    """移除一个 provider 不影响其余 provider 的注册与可见名单。"""
    registry, provider = _make_registry_with_provider()
    other = _EchoProvider("other")
    registry.register_provider(other)

    assert registry.unregister_provider(provider) == 2
    assert registry.has("other_alpha") and registry.has("other_beta")
    assert other in registry._providers
    assert registry.provider_of_tool("other_alpha") is other
