"""Provider 连接与生命周期：重连契约、reconnect_provider、工具集刷新、批量启停、unregister 一致移除"""

from __future__ import annotations

from typing import List

import pytest

from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider, make_provider_from_specs
from src.modules.tools.registry import ToolRegistry, _ToolHealth


class _Minimal(BaseToolProvider):
    """不覆写 connect：supports_reconnect=False。"""

    def __init__(self, name: str = "minimal") -> None:
        self._name = name
        self.tool_names: List[str] = ["x"]

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self):
        return [ToolSpec(name=n, description=n, kind="sync", provider=self._name) for n in self.tool_names]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def health_check(self) -> bool:
        return True


class _Connectable(_Minimal):
    """覆写 connect/disconnect/setup/cleanup 并计数；工具集可变（模拟 MCP 降级登记 → 恢复换血）。"""

    category = "mcp"

    def __init__(self, name: str = "conn", *, connect_ok: bool = True, setup_error: Exception | None = None) -> None:
        super().__init__(name)
        self.tool_names = ["a"]
        self.connect_ok = connect_ok
        self.setup_error = setup_error
        self.connect_calls = self.disconnect_calls = self.setup_calls = self.cleanup_calls = 0

    async def connect(self) -> bool:
        self.connect_calls += 1
        return self.connect_ok

    async def disconnect(self) -> bool:
        self.disconnect_calls += 1
        return True

    async def setup(self) -> None:
        self.setup_calls += 1
        if self.setup_error is not None:
            raise self.setup_error

    async def cleanup(self) -> None:
        self.cleanup_calls += 1


class _SelfManaged(_Connectable):
    manages_own_lifecycle = True  # MCP 形态：批量启停应跳过


class _NonBase:
    """纯 duck-typed Provider，不继承 BaseToolProvider。"""

    name = "legacy"
    category = ""

    def list_tools(self):
        return [ToolSpec(name="legacy_t", description="lt", kind="sync", provider="legacy")]

    async def invoke(self, invocation: ToolInvocation):
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


def _tripped() -> _ToolHealth:
    return _ToolHealth(consecutive_failures=3, tripped=True, tripped_at_ms=1, last_error="boom")


# 连接动作契约


async def test_default_connection_actions_and_supports_reconnect_single_point() -> None:
    minimal = _Minimal()
    assert (await minimal.connect(), await minimal.disconnect(), await minimal.reconnect()) == (False, False, False)
    assert minimal.supports_reconnect is False
    assert _Connectable().supports_reconnect is True  # 覆写 connect 即自动启用

    async def echo(inv: ToolInvocation):
        return ToolExecutionResult(tool_name=inv.tool_name, success=True, content="ok")

    spec_only = make_provider_from_specs("builtin", [(ToolSpec(name="e", description="e", provider="builtin"), echo)])
    assert spec_only.supports_reconnect is False  # 无状态 Provider 无重连按钮

    connectable = _Connectable()
    assert await connectable.reconnect() is True  # 默认组合：先 disconnect 再 connect
    assert (connectable.disconnect_calls, connectable.connect_calls) == (1, 1)

    class _FullOverride(_Connectable):
        async def reconnect(self) -> bool:
            return await self.connect()

    override = _FullOverride()
    assert await override.reconnect() is True and override.disconnect_calls == 0  # 整体覆写不走默认组合


def test_registry_provider_supports_reconnect(registry: ToolRegistry) -> None:
    registry.register_provider(_Connectable())
    registry.register_provider(_Minimal())
    registry.register_provider(_NonBase())  # type: ignore[arg-type]
    assert registry.provider_supports_reconnect("conn_a") is True
    assert [registry.provider_supports_reconnect(n) for n in ("minimal_x", "legacy_t", "ghost")] == [False] * 3


# reconnect_provider


async def test_reconnect_provider_error_reports(registry: ToolRegistry) -> None:
    report = await registry.reconnect_provider("nonexistent")
    assert report["ok"] is False and "未注册 Provider" in report["error"] and "nonexistent" in report["error"]

    registry.register_provider(_Minimal())
    registry.register_provider(_NonBase())  # type: ignore[arg-type]
    for provider_id in ("minimal", "legacy"):  # 不支持重连时不触发任何重连动作
        report = await registry.reconnect_provider(provider_id)
        assert report["ok"] is False and "不支持手动重连" in report["error"]

    failing = _Connectable("failing", connect_ok=False)
    registry.register_provider(failing)
    report = await registry.reconnect_provider("failing")
    assert (report["ok"], report["provider_id"], failing.connect_calls) == (False, "failing", 1)
    assert "重连失败" in report["error"]

    class _Boom(_Connectable):
        async def connect(self) -> bool:
            raise RuntimeError("kaboom")

    registry.register_provider(_Boom("boom"))
    report = await registry.reconnect_provider("boom")  # 重连抛异常兜底为 ok=False，不上抛
    assert report["ok"] is False and "kaboom" in report["error"] and report["provider_id"] == "boom"


async def test_reconnect_success_classifies_tripped_tools_by_probe(registry: ToolRegistry) -> None:
    provider = _Connectable()
    provider.tool_names = ["a", "a_fail"]  # 重连刷新换血后两者都存续，熔断历史不因刷新洗白
    registry.register_provider(provider)
    registry._health["conn_a"] = _tripped()
    registry._health["conn_a_fail"] = _tripped()
    results = iter([True, False])  # 按工具顺序探活：先通过后失败

    async def probe() -> bool:
        return next(results)

    provider.health_check = probe  # type: ignore[method-assign]
    report = await registry.reconnect_provider("conn")
    assert (report["ok"], report["provider_id"]) == (True, "conn")
    assert (report["recovered"], report["still_tripped"]) == (["conn_a"], ["conn_a_fail"])
    assert not registry.is_tripped("conn_a") and registry.is_tripped("conn_a_fail")

    calm = ToolRegistry()
    calm.register_provider(_Connectable())
    report = await calm.reconnect_provider("conn")
    assert (report["ok"], report["recovered"], report["still_tripped"]) == (True, [], [])


async def test_reconnect_refreshes_degraded_toolset_and_survives_refresh_failure(registry: ToolRegistry) -> None:
    provider = _Connectable()
    provider.tool_names = []
    registry.register_provider(provider)  # 0 工具降级登记
    assert registry.list_tools(provider="conn") == []
    provider.tool_names = ["a", "b"]
    report = await registry.reconnect_provider("conn")
    assert report["ok"] is True and sorted(report["refreshed"]["added"]) == ["conn_a", "conn_b"]
    assert {s.full_name for s in registry.list_tools(provider="conn")} == {"conn_a", "conn_b"}

    class _BoomOnRefresh(_Connectable):
        boom = False

        def list_tools(self):
            if self.boom:
                raise RuntimeError("list boom")
            return []

    fragile = _BoomOnRefresh("fragile")
    registry.register_provider(fragile)
    fragile.boom = True
    report = await registry.reconnect_provider("fragile")  # 刷新抛异常不推翻重连成果
    assert report["ok"] is True and report["refreshed"] is None


# 工具集刷新与可见名单重派


def test_zero_tool_provider_keeps_record_and_refresh_resyncs_specs(registry: ToolRegistry) -> None:
    empty = _Connectable("empty")
    empty.tool_names = []
    assert registry.register_provider(empty) == 0
    record = {r["name"]: r for r in registry.list_providers()}["empty"]
    assert (record["tool_count"], record["category"]) == (0, "mcp")  # 降级登记：工具页可见但无工具条目

    provider = _Connectable("mprov")
    provider.tool_names = ["a", "b"]
    registry.register_provider(provider)
    provider.tool_names = ["b", "c"]
    report = registry.refresh_provider_tools(provider)
    assert (report["ok"], report["added"], report["removed"], report["count"]) == (True, ["mprov_c"], ["mprov_a"], 2)
    assert {s.full_name for s in registry.list_tools(provider="mprov")} == {"mprov_b", "mprov_c"}

    stranger = registry.refresh_provider_tools(_Connectable("stranger"))
    assert stranger["ok"] is False and "未注册 Provider" in stranger["error"]


def test_refresh_rederives_visibility_and_keeps_persisting_tripped_state(registry: ToolRegistry) -> None:
    provider = _Connectable("mprov")
    provider.tool_names = ["a", "gone"]
    calls: List[int] = []

    def policy(specs):
        calls.append(len(specs))
        return {s.full_name: ["minecraft"] for s in specs}

    registry.register_provider(provider, visible_to=policy)
    registry._health["mprov_a"] = _tripped()
    registry._health["mprov_gone"] = _tripped()

    provider.tool_names = ["a", "new_tool"]
    report = registry.refresh_provider_tools(provider)
    assert report["ok"] is True and report["removed"] == ["mprov_gone"]
    assert registry.visible_to_of("mprov_new_tool") == ["minecraft"] and len(calls) == 2  # 新工具按策略重派，不落默认
    assert registry.is_tripped("mprov_a")  # 存续工具保留熔断待探活状态
    assert not registry.has("mprov_gone") and "mprov_gone" not in registry._health  # 消失工具连带清除

    static = _Connectable("sprov")
    static.tool_names = ["a", "gone"]
    registry.register_provider(static, visible_to={"sprov_a": ["streamer"], "sprov_gone": ["streamer"]})
    static.tool_names = ["a"]
    assert registry.refresh_provider_tools(static)["ok"] is True  # 静态名单过滤已消失的键，不抛错
    assert registry.visible_to_of("sprov_a") == ["streamer"]


# 批量启停


async def test_start_providers_skips_self_managed_isolates_failures_and_is_idempotent(registry: ToolRegistry) -> None:
    good, bad = _Connectable("good"), _Connectable("bad", setup_error=RuntimeError("连接失败"))
    managed = _SelfManaged("managed")
    for provider in (good, bad, managed):
        assert registry.register_provider(provider) == 1

    await registry.stop_providers()  # 未启动先停：安全短路
    assert good.cleanup_calls == 0

    assert await registry.start_providers() == {"bad": False, "good": True}  # 自管理跳过，单个失败不阻断其余
    assert (good.setup_calls, bad.setup_calls, managed.setup_calls) == (1, 1, 0)
    assert await registry.start_providers() == {} and good.setup_calls == 1  # 幂等

    await registry.stop_providers()
    assert (good.cleanup_calls, managed.cleanup_calls) == (1, 0)  # 只收尾已启动者


# unregister


def test_unregister_removes_every_internal_map_idempotently_and_keeps_others(registry: ToolRegistry) -> None:
    provider = _Minimal("echo")
    provider.tool_names = ["alpha", "beta"]
    provider.category = "test"
    other = _Minimal("other")
    registry.register_provider(provider, visible_to={"echo_alpha": ["minecraft"]})
    registry.register_provider(other)

    assert registry.unregister_provider(provider) == 2
    assert provider not in registry._providers and not registry.has("echo_alpha") and not registry.has("echo_beta")
    assert "echo_alpha" not in registry._tool_owner and "echo_alpha" not in registry._visible_to
    assert "echo" not in registry._categories
    assert registry.visible_to_of("echo_alpha") == ["streamer"] and registry.provider_of_tool("echo_alpha") is None
    assert registry.unregister_provider(provider) == 0  # 重复移除幂等

    assert registry.has("other_x") and other in registry._providers  # 其余 Provider 不受影响
    assert registry.provider_of_tool("other_x") is other
    assert all(r["name"] != "echo" for r in registry.list_providers())
