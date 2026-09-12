"""
ToolHealthMonitor + ToolRegistry 熔断器单元测试

覆盖：
- 连续失败达阈值熔断（threshold=2）
- 熔断工具默认从 list_tools / to_llm_definitions 摘除，include_tripped=True 时可见
- 熔断短路返回失败 result 且不 emit tool.result.<name>
- 成功重置连续失败计数（fail, success, fail → 不熔断）
- 熔断时 emit tool.health.<name>（state=open）；recover_tool emit state=closed
- registry.probe_tool 默认 BaseToolProvider 返回 True / override 返回 False / 抛异常
- threshold<=0 永不熔断
- monitor probe_cycle: 探活通过恢复；探活失败保持；默认 health_check 走"探活即恢复"；
  未到驻留时长跳过

测试镜像 tests/modules/tools/test_tool_registry.py 的 fixture 风格
（pytest-asyncio；EventBus + ToolRegistry 临时构造）。
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.tool_health import ToolHealthPayload
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.tools import (
    ToolExecutionResult,
    ToolHealthMonitor,
    ToolInvocation,
    ToolRegistry,
    ToolSpec,
)
from src.modules.tools.provider import BaseToolProvider


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
async def event_bus() -> EventBus:
    bus = EventBus(enable_stats=False)
    yield bus
    await bus.cleanup()


def _ok(inv: ToolInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(tool_name=inv.tool_name, success=True, content="ok")


def _bad(inv: ToolInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=inv.tool_name,
        success=False,
        error_message="bad",
    )


async def _bad_async(inv: ToolInvocation) -> ToolExecutionResult:
    return _bad(inv)


async def _ok_async(inv: ToolInvocation) -> ToolExecutionResult:
    return _ok(inv)


async def _flush() -> None:
    """让 emit 的 fire-and-forget 派发任务跑完。"""
    await asyncio.sleep(0.02)


# =============================================================================
# Registry：熔断阈值 + 摘除行为
# =============================================================================


async def test_consecutive_failures_trip_at_threshold() -> None:
    """连续失败达阈值（threshold=2）即熔断。"""
    registry = ToolRegistry(failure_threshold=2)
    spec = ToolSpec(name="flaky", description="d", kind="sync", provider="test")
    registry.register(spec, _bad_async)

    inv = ToolInvocation(tool_name="test_flaky", source="t")
    await registry.invoke(inv)
    assert not registry.is_tripped("test_flaky"), "1st 失败未达阈值"
    await registry.invoke(inv)
    assert registry.is_tripped("test_flaky"), "2nd 失败应熔断"
    assert "test_flaky" in registry.tripped_tools


async def test_tripped_tool_filtered_from_default_list() -> None:
    """熔断工具默认从 list_tools 摘除；include_tripped=True 时可见。"""
    registry = ToolRegistry(failure_threshold=1)
    spec = ToolSpec(name="t1", description="d", kind="sync", provider="p")
    registry.register(spec, _bad_async)

    await registry.invoke(ToolInvocation(tool_name="p_t1"))
    assert registry.is_tripped("p_t1")

    visible_default = {s.full_name for s in registry.list_tools()}
    assert "p_t1" not in visible_default, "默认应摘除"

    visible_all = {s.full_name for s in registry.list_tools(include_tripped=True)}
    assert "p_t1" in visible_all


async def test_tripped_tool_filtered_from_to_llm_definitions() -> None:
    """LLM 视角定义默认隐藏熔断工具——planner 看不见。"""
    registry = ToolRegistry(failure_threshold=1)
    registry.register(ToolSpec(name="trip", description="d", kind="sync", provider="p"), _bad_async)

    await registry.invoke(ToolInvocation(tool_name="p_trip"))
    assert registry.is_tripped("p_trip")
    defs = registry.to_llm_definitions()
    assert all(d["name"] != "p_trip" for d in defs)


async def test_invoke_tripped_returns_failure_no_tool_result_event() -> None:
    """熔断短路：返回失败 result，不 emit tool.result.<name>。"""
    bus = EventBus(enable_stats=False)
    try:
        results: List[ToolResultPayload] = []

        async def _on_result(name: str, payload: ToolResultPayload, source: str) -> None:
            results.append(payload)

        bus.on("tool.result.#", _on_result, ToolResultPayload)

        registry = ToolRegistry(event_bus=bus, failure_threshold=1)
        registry.register(ToolSpec(name="x", description="d", kind="sync", provider="p"), _bad_async)
        # 第 1 次失败 → 熔断
        await registry.invoke(ToolInvocation(tool_name="p_x"))
        await _flush()
        results.clear()

        # 熔断后再 invoke：失败 result，不 emit
        res = await registry.invoke(ToolInvocation(tool_name="p_x"))
        assert res.success is False
        assert "熔断" in res.error_message
        await _flush()
        assert results == [], "熔断短路不应 emit tool.result"
    finally:
        await bus.cleanup()


async def test_success_resets_consecutive_counter() -> None:
    """成功重置连续失败计数（threshold=2 时 fail,success,fail 仍不熔断）。"""
    registry = ToolRegistry(failure_threshold=2)
    spec = ToolSpec(name="flip", description="d", kind="sync", provider="p")

    async def _flip(inv: ToolInvocation) -> ToolExecutionResult:
        # 交替 fail/success
        _flip.calls += 1
        if _flip.calls % 2 == 1:
            return _bad(inv)
        return _ok(inv)

    _flip.calls = 0
    registry.register(spec, _flip)

    await registry.invoke(ToolInvocation(tool_name="p_flip"))  # fail (count=1)
    await registry.invoke(ToolInvocation(tool_name="p_flip"))  # success (count=0)
    await registry.invoke(ToolInvocation(tool_name="p_flip"))  # fail (count=1)
    assert not registry.is_tripped("p_flip")
    snap = registry.tool_health_snapshot()
    entry = snap.get("p_flip")
    assert entry is not None and entry["state"] == "healthy"
    assert entry["failure_count"] == 1


async def test_trip_emits_open_event_recover_emits_closed_event() -> None:
    """熔断→emit state=open；recover_tool→emit state=closed。"""
    bus = EventBus(enable_stats=False)
    try:
        health_events: List[ToolHealthPayload] = []

        async def _on(name: str, payload: ToolHealthPayload, source: str) -> None:
            health_events.append(payload)

        bus.on("tool.health.#", _on, ToolHealthPayload)

        registry = ToolRegistry(event_bus=bus, failure_threshold=1)
        registry.register(ToolSpec(name="z", description="d", kind="sync", provider="p"), _bad_async)
        await registry.invoke(ToolInvocation(tool_name="p_z"))
        await _flush()
        assert any(p.state == "open" and p.tool_name == "p_z" for p in health_events)

        registry.recover_tool("p_z")
        await _flush()
        assert any(p.state == "closed" and p.tool_name == "p_z" for p in health_events)
        assert not registry.is_tripped("p_z")
    finally:
        await bus.cleanup()


async def test_threshold_zero_disables_tripping() -> None:
    """failure_threshold<=0：失败任意次都不熔断，状态仍记录。"""
    registry = ToolRegistry(failure_threshold=0)
    registry.register(ToolSpec(name="never", description="d", kind="sync", provider="p"), _bad_async)
    for _ in range(10):
        await registry.invoke(ToolInvocation(tool_name="p_never"))
    assert not registry.is_tripped("p_never")
    snap = registry.tool_health_snapshot()
    assert "p_never" in snap
    assert snap["p_never"]["state"] == "healthy"
    assert snap["p_never"]["failure_count"] == 10


# =============================================================================
# Registry.probe_tool —— 新版探活入口（替换旧的反射式 provider_health_check）
# =============================================================================


async def test_probe_tool_returns_true_for_default_base_provider() -> None:
    """默认 BaseToolProvider（无重写）→ probe_tool 返回 True。"""

    class _DefaultHealthProvider(BaseToolProvider):
        category = "test"

        @property
        def name(self) -> str:
            return "DefaultHealth"

        def list_tools(self):
            return [ToolSpec(name="dtool", description="d", kind="sync", provider="DefaultHealth")]

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    registry = ToolRegistry()
    registry.register_provider(_DefaultHealthProvider())

    assert await registry.probe_tool("DefaultHealth_dtool") is True


async def test_probe_tool_returns_override_value() -> None:
    """MCP-style Provider 重写 health_check → probe_tool 返回重写的值。"""

    class _McpStyleProvider(BaseToolProvider):
        category = "test"

        def __init__(self, healthy: bool) -> None:
            self._healthy = healthy
            self.calls = 0

        @property
        def name(self) -> str:
            return "McpStyle"

        def list_tools(self):
            return [ToolSpec(name="mtool", description="d", kind="sync", provider="McpStyle")]

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

        async def health_check(self) -> bool:
            self.calls += 1
            return self._healthy

    registry = ToolRegistry()
    prov = _McpStyleProvider(healthy=False)
    registry.register_provider(prov)
    assert await registry.probe_tool("McpStyle_mtool") is False
    assert prov.calls == 1

    prov._healthy = True
    assert await registry.probe_tool("McpStyle_mtool") is True


async def test_probe_tool_returns_false_when_health_check_raises() -> None:
    """provider.health_check 抛异常 → probe_tool 返回 False（不外抛）。"""

    class _BoomProvider(BaseToolProvider):
        category = "test"

        @property
        def name(self) -> str:
            return "Boom"

        def list_tools(self):
            return [ToolSpec(name="btool", description="d", kind="sync", provider="Boom")]

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

        async def health_check(self) -> bool:
            raise RuntimeError("probe boom")

    registry = ToolRegistry()
    registry.register_provider(_BoomProvider())
    assert await registry.probe_tool("Boom_btool") is False


async def test_probe_tool_unknown_tool_returns_true() -> None:
    """未知工具名 → 无归属 provider → 按默认语义返回 True（让流量决定）。"""
    registry = ToolRegistry()
    assert await registry.probe_tool("ghost") is True


async def test_probe_tool_plain_register_returns_true() -> None:
    """``register()`` 直注册（无归属 provider）→ 按默认语义返回 True。

    基类默认探活语义："无可检查之物，让流量决定"——熔断后冷却期满
    即恢复，再失败再熔断。与旧定时恢复路径时序一致。
    """
    registry = ToolRegistry()
    spec = ToolSpec(name="lonely", description="d", kind="sync", provider="nowhere")
    registry.register(spec, _ok_async)
    assert await registry.probe_tool("nowhere_lonely") is True


async def test_probe_tool_resolves_owner_via_registration_map() -> None:
    """探活归属走注册时记录的全名→Provider 映射（对象引用，非名字匹配）。

    命名模型下 provider.name 与 spec.provider 同值同源（注册期 fail-fast
    校验，见 test_tool_registry 的不匹配拒绝用例）；本用例固定探活按
    所有权映射命中并返回 Provider 重写的 health_check 值。
    """

    class _OwnedProbeProvider(BaseToolProvider):
        category = "test"

        @property
        def name(self) -> str:
            return "realprov"

        def list_tools(self):
            return [ToolSpec(name="tool", description="d", kind="sync", provider="realprov")]

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

        async def health_check(self) -> bool:
            return False

    registry = ToolRegistry()
    registry.register_provider(_OwnedProbeProvider())

    assert await registry.probe_tool("realprov_tool") is False


async def test_register_non_base_provider_logs_warning() -> None:
    """register_provider 接非 BaseToolProvider 子类 → 记 warning（仍注册）"""
    from loguru import logger

    class _LegacyProvider:
        """duck-typed Provider（不继承 BaseToolProvider）—— 迁移遗留示意。"""

        @property
        def name(self) -> str:
            return "Legacy"

        def list_tools(self):
            return [ToolSpec(name="ltool", description="d", kind="sync", provider="Legacy")]

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    registry = ToolRegistry()
    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(m), level="WARNING")
    try:
        registry.register_provider(_LegacyProvider())  # type: ignore[arg-type]
    finally:
        logger.remove(handler_id)

    assert any("非 BaseToolProvider 子类" in m for m in messages)

    # 迁移遗留：duck-typed Provider 未进所有权映射 → 无归属 → 按默认语义返回 True
    assert await registry.probe_tool("Legacy_ltool") is True


# =============================================================================
# ToolHealthMonitor：probe_cycle
# =============================================================================


class _ProbeProvider(BaseToolProvider):
    """测试用 provider：health_check 可控返回。"""

    def __init__(self, returns: bool, raises: bool = False) -> None:
        self._returns = returns
        self._raises = raises

    @property
    def name(self) -> str:
        return "probe"

    category = "test"

    def list_tools(self):
        return [ToolSpec(name="ptool", description="d", kind="sync", provider="probe")]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def health_check(self) -> bool:
        if self._raises:
            raise RuntimeError("probe boom")
        return self._returns


async def _force_trip(registry: ToolRegistry, name: str, threshold: int = 1) -> None:
    """帮助函数：让指定工具快速熔断。"""
    health = registry._ensure_health(name)
    health.consecutive_failures = threshold
    health.tripped = True
    health.tripped_at_ms = 0  # 极早驻留确保超过 dwell


async def test_probe_cycle_healthy_probe_recovers() -> None:
    """monitor.probe_cycle：provider health_check=True → 恢复。"""
    registry = ToolRegistry()
    prov = _ProbeProvider(returns=True)
    registry.register_provider(prov)
    await _force_trip(registry, "probe_ptool")

    monitor = ToolHealthMonitor(registry, probe_interval_ms=10)
    await monitor.probe_cycle()
    assert not registry.is_tripped("probe_ptool")


async def test_probe_cycle_unhealthy_probe_stays_tripped() -> None:
    """monitor.probe_cycle：health_check=False → 维持熔断。"""
    registry = ToolRegistry()
    prov = _ProbeProvider(returns=False)
    registry.register_provider(prov)
    await _force_trip(registry, "probe_ptool")

    monitor = ToolHealthMonitor(registry, probe_interval_ms=10)
    await monitor.probe_cycle()
    assert registry.is_tripped("probe_ptool")


async def test_probe_cycle_default_health_check_recovers() -> None:
    """monitor.probe_cycle：使用默认 health_check（never overridden）→ 探活即恢复。

    旧版"无 health_check → 定时恢复"路径的语义等价物：默认 ``BaseToolProvider.health_check``
    返回 True，monitor 经 ``probe_tool`` 拿到 True 后调 ``recover_tool`` 复位熔断。
    """
    registry = ToolRegistry()

    class _StatelessProvider(BaseToolProvider):
        category = "test"

        @property
        def name(self) -> str:
            return "stateless"

        def list_tools(self):
            return [ToolSpec(name="stool", description="d", kind="sync", provider="stateless")]

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    registry.register_provider(_StatelessProvider())
    await _force_trip(registry, "stateless_stool")

    monitor = ToolHealthMonitor(registry, probe_interval_ms=10)
    await monitor.probe_cycle()
    assert not registry.is_tripped("stateless_stool")


async def test_probe_cycle_respects_minimum_dwell() -> None:
    """驻留时长未到 → 本轮跳过，不探活。"""
    registry = ToolRegistry(failure_threshold=1)
    prov = _ProbeProvider(returns=True)
    registry.register_provider(prov)
    # 真实熔断（threshold=1）
    registry.register(ToolSpec(name="dwell", description="d", kind="sync", provider="probe"), _bad_async)
    await registry.invoke(ToolInvocation(tool_name="probe_dwell"))
    assert registry.is_tripped("probe_dwell")

    monitor = ToolHealthMonitor(registry, probe_interval_ms=10_000)  # 极长 dwell
    await monitor.probe_cycle()
    assert registry.is_tripped("probe_dwell"), "dwell 未到不应恢复"


async def test_probe_cycle_probe_exception_logs_and_stays_tripped() -> None:
    """探活抛异常 → 视为不健康，继续熔断。"""
    registry = ToolRegistry()
    prov = _ProbeProvider(returns=False, raises=True)
    registry.register_provider(prov)
    await _force_trip(registry, "probe_ptool")

    monitor = ToolHealthMonitor(registry, probe_interval_ms=10)
    await monitor.probe_cycle()
    assert registry.is_tripped("probe_ptool")


# =============================================================================
# 工具健康快照：Dashboard 行为契约
# =============================================================================


async def test_health_snapshot_only_includes_tripped_or_failed_tools() -> None:
    """snapshot 仅包含熔断中或近期失败工具；其余视为健康（缺席键）。"""
    registry = ToolRegistry()
    registry.register(ToolSpec(name="clean", description="d", kind="sync", provider="p"), _ok_async)
    registry.register(ToolSpec(name="dirty", description="d", kind="sync", provider="p"), _bad_async)

    await registry.invoke(ToolInvocation(tool_name="p_clean"))
    snap1 = registry.tool_health_snapshot()
    assert snap1 == {}

    await registry.invoke(ToolInvocation(tool_name="p_dirty"))
    snap2 = registry.tool_health_snapshot()
    assert "p_dirty" in snap2
    assert snap2["p_dirty"]["state"] == "healthy"  # 未达阈值但有失败
    assert "p_clean" not in snap2


async def test_invoke_unknown_tool_does_not_touch_health() -> None:
    """未知/停用短路不触达健康状态（与现有契约一致）。"""
    registry = ToolRegistry()
    await registry.invoke(ToolInvocation(tool_name="ghost"))
    assert registry.tool_health_snapshot() == {}
    assert not registry.is_tripped("ghost")
