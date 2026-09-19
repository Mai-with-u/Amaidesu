"""ToolRegistry Provider 常驻登记与工具集刷新（refresh_provider_tools）单测。

覆盖：
- 0 工具降级登记：Provider 留在 list_providers（工具页可见），不产生工具条目
- 工具集刷新：按 provider 当前 list_tools() 换血（新增 / 移除），报告准确
- 可见名单重派：策略 callable 对新工具集重跑（fail-closed 不落"未列出=全员"默认）；
  静态 dict 过滤已消失键后沿用
- 熔断历史：存续工具保留"待探活复位"，消失工具连带清除
- 未注册 Provider 刷新 → ok=False；unregister 清理登记记录
- reconnect_provider 联动刷新：降级 Provider 重连成功即补注册
"""

from __future__ import annotations

from typing import List

import pytest

from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.tools.registry import ToolRegistry


class _MutableProvider(BaseToolProvider):
    """工具集可变 + 连接可模拟的 Provider（模拟 MCP 降级登记 → 恢复换血）。"""

    category = "mcp"

    def __init__(self, name: str = "mprov") -> None:
        self._name = name
        self.tool_names: List[str] = []
        self.connect_ok = True

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self):
        return [ToolSpec(name=n, description=f"desc {n}", kind="sync", provider=self._name) for n in self.tool_names]

    async def invoke(self, invocation: ToolInvocation):
        from src.modules.tools.models import ToolExecutionResult

        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def health_check(self) -> bool:
        return True

    async def connect(self) -> bool:
        return self.connect_ok

    async def disconnect(self) -> bool:
        return True


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


def test_register_zero_tool_provider_keeps_record(registry: ToolRegistry) -> None:
    """0 工具 Provider 照常登记：list_providers 在列（降级可见），无工具条目。"""
    provider = _MutableProvider()
    new_count = registry.register_provider(provider)
    assert new_count == 0
    records = {r["name"]: r for r in registry.list_providers()}
    assert records["mprov"]["tool_count"] == 0
    assert records["mprov"]["category"] == "mcp"
    assert registry.list_tools(provider="mprov") == []


def test_refresh_provider_tools_resyncs_specs(registry: ToolRegistry) -> None:
    """工具集换血：按 provider 当前 list_tools 重新登记，报告新增与移除。"""
    provider = _MutableProvider()
    provider.tool_names = ["a", "b"]
    registry.register_provider(provider)

    provider.tool_names = ["b", "c"]
    report = registry.refresh_provider_tools(provider)
    assert report["ok"] is True
    assert report["added"] == ["mprov_c"]
    assert report["removed"] == ["mprov_a"]
    assert report["count"] == 2
    names = {s.full_name for s in registry.list_tools(provider="mprov")}
    assert names == {"mprov_b", "mprov_c"}


def test_refresh_rederives_visibility_policy_for_new_tools(registry: ToolRegistry) -> None:
    """策略 callable 注册：刷新后对新工具重派名单，不落"未列出=全员"默认。"""
    provider = _MutableProvider()
    provider.tool_names = ["a"]
    policy_calls: List[int] = []

    def _policy(specs):
        policy_calls.append(len(specs))
        return {s.full_name: ["minecraft"] for s in specs}

    registry.register_provider(provider, visible_to=_policy)
    assert registry.visible_to_of("mprov_a") == ["minecraft"]

    provider.tool_names = ["a", "new_tool"]
    report = registry.refresh_provider_tools(provider)
    assert report["ok"] is True
    # fail-closed：新工具按策略重派为仅 minecraft 可见，而不是默认全员
    assert registry.visible_to_of("mprov_new_tool") == ["minecraft"]
    assert len(policy_calls) == 2, "策略对注册期与刷新期各求值一次"


def test_refresh_static_visible_dict_drops_stale_keys(registry: ToolRegistry) -> None:
    """静态名单注册：刷新时已消失的工具键被过滤（告警不抛错），存续键沿用。"""
    provider = _MutableProvider()
    provider.tool_names = ["a", "gone"]
    registry.register_provider(
        provider,
        visible_to={"mprov_a": ["streamer"], "mprov_gone": ["streamer"]},
    )
    provider.tool_names = ["a"]
    report = registry.refresh_provider_tools(provider)
    assert report["ok"] is True
    assert registry.visible_to_of("mprov_a") == ["streamer"]


def test_refresh_preserves_tripped_state_for_persisting_tools(registry: ToolRegistry) -> None:
    """存续工具的熔断状态不因刷新洗白；消失工具的熔断状态连带清除。"""
    from src.modules.tools.registry import _ToolHealth

    provider = _MutableProvider()
    provider.tool_names = ["a", "gone"]
    registry.register_provider(provider)
    registry._health["mprov_a"] = _ToolHealth(consecutive_failures=3, tripped=True, tripped_at_ms=1, last_error="boom")
    registry._health["mprov_gone"] = _ToolHealth(
        consecutive_failures=3, tripped=True, tripped_at_ms=1, last_error="boom"
    )

    provider.tool_names = ["a"]
    report = registry.refresh_provider_tools(provider)
    assert report["ok"] is True
    assert report["removed"] == ["mprov_gone"]
    assert registry.is_tripped("mprov_a"), "存续工具保留熔断待探活状态"
    assert not registry.has("mprov_gone")
    assert "mprov_gone" not in registry._health


def test_refresh_unknown_provider_returns_error(registry: ToolRegistry) -> None:
    """未注册 Provider → ok=False（refresh 只作用于常驻登记的 Provider）。"""
    provider = _MutableProvider()
    report = registry.refresh_provider_tools(provider)
    assert report["ok"] is False
    assert "未注册 Provider" in report["error"]


def test_unregister_clears_provider_record(registry: ToolRegistry) -> None:
    """unregister_provider 清理登记记录（name 从 list_providers 消失）。"""
    provider = _MutableProvider()
    registry.register_provider(provider)
    registry.unregister_provider(provider)
    assert all(r["name"] != "mprov" for r in registry.list_providers())


async def test_reconnect_provider_refreshes_degraded_toolset(registry: ToolRegistry) -> None:
    """降级登记（0 工具）的 Provider 手动重连成功 → 工具集补注册进报告。"""
    provider = _MutableProvider()
    registry.register_provider(provider)
    assert registry.list_tools(provider="mprov") == []

    provider.tool_names = ["a", "b"]
    report = await registry.reconnect_provider("mprov")
    assert report["ok"] is True
    assert report["refreshed"] is not None
    assert sorted(report["refreshed"]["added"]) == ["mprov_a", "mprov_b"]
    names = {s.full_name for s in registry.list_tools(provider="mprov")}
    assert names == {"mprov_a", "mprov_b"}


async def test_reconnect_provider_refresh_exception_still_ok(registry: ToolRegistry) -> None:
    """刷新抛异常不推翻重连成果：报告 ok=True 且 refreshed=None（携带日志）。"""

    class _BoomOnRefresh(_MutableProvider):
        boom = False

        def list_tools(self):
            if self.boom:
                raise RuntimeError("list boom")
            return []

    provider = _BoomOnRefresh()
    registry.register_provider(provider)  # 0 工具降级登记（不炸）
    provider.boom = True
    report = await registry.reconnect_provider("mprov")
    assert report["ok"] is True
    assert report["refreshed"] is None
