"""工具熔断器：连续失败熔断、摘除与短路、健康事件、探活入口 probe_tool、监控器 probe_cycle、健康快照"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import pytest
from loguru import logger

from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.tool_health import ToolHealthPayload
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.tools import ToolExecutionResult, ToolHealthMonitor, ToolInvocation, ToolRegistry, ToolSpec
from src.modules.tools.llm_defs import to_llm_definitions
from src.modules.tools.operations import ToolOperations
from src.modules.tools.provider import BaseToolProvider


def _ops(registry: ToolRegistry) -> ToolOperations:
    return ToolOperations(registry)


async def _ok(inv: ToolInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(tool_name=inv.tool_name, success=True, content="ok")


async def _bad(inv: ToolInvocation) -> ToolExecutionResult:
    return ToolExecutionResult(tool_name=inv.tool_name, success=False, error_message="bad")


async def _flush() -> None:
    await asyncio.sleep(0.02)  # 让 emit 的 fire-and-forget 派发任务跑完（用于断言"没有事件"的场景）


async def _until(condition, timeout: float = 2.0) -> None:
    """轮询到条件成立：emit 是 fire-and-forget 派发，满载时固定睡眠可能赶不上，断言"有事件"时改等条件。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)


def _registry_with(name: str, impl=_bad, provider: str = "p", **kwargs) -> ToolRegistry:
    registry = ToolRegistry(**kwargs)
    registry.register(ToolSpec(name=name, description="d", kind="sync", provider=provider), impl)
    return registry


class _Provider(BaseToolProvider):
    """health_check 可控的单工具 Provider：healthy=None 表示不重写（走基类默认探活）。"""

    category = "test"

    def __init__(self, name: str = "probe", healthy: Optional[bool] = None, raises: bool = False) -> None:
        self._name = name
        self._healthy = healthy
        self._raises = raises
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self):
        return [ToolSpec(name="tool", description="d", kind="sync", provider=self._name)]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def health_check(self) -> bool:
        self.calls += 1
        if self._raises:
            raise RuntimeError("probe boom")
        return True if self._healthy is None else self._healthy


def _force_trip(registry: ToolRegistry, name: str) -> None:
    health = registry._health.state(name)
    health.consecutive_failures = 1
    health.tripped = True
    health.tripped_at_ms = 0  # 极早熔断，保证已超过最短驻留


# 熔断阈值、摘除与短路


async def test_failures_trip_at_threshold_and_hide_tool_from_default_views() -> None:
    registry = _registry_with("flaky", failure_threshold=2)
    inv = ToolInvocation(tool_name="p_flaky", source="t")
    await registry.invoke(inv)
    assert not registry.is_tripped("p_flaky")  # 第 1 次失败未达阈值
    await registry.invoke(inv)
    assert registry.is_tripped("p_flaky") and "p_flaky" in registry.tripped_tools

    # 熔断工具默认从工具列表与 LLM 定义摘除（planner 看不见），include_tripped 时可见
    assert "p_flaky" not in {s.full_name for s in registry.list_tools()}
    assert "p_flaky" in {s.full_name for s in registry.list_tools(include_tripped=True)}
    assert all(d["name"] != "p_flaky" for d in to_llm_definitions(registry))


async def test_tripped_invoke_short_circuits_without_tool_result_event() -> None:
    bus = EventBus(enable_stats=False)
    results: List[ToolResultPayload] = []

    async def on_result(name: str, payload: ToolResultPayload, source: str) -> None:
        results.append(payload)

    bus.on("tool.result.#", on_result, ToolResultPayload)
    registry = _registry_with("x", event_bus=bus, failure_threshold=1)
    try:
        await registry.invoke(ToolInvocation(tool_name="p_x"))  # 第 1 次失败即熔断
        await _until(lambda: len(results) == 1)
        results.clear()
        res = await registry.invoke(ToolInvocation(tool_name="p_x"))
        await _flush()
        assert res.success is False and "熔断" in res.error_message
        assert results == []  # 熔断短路不广播 tool.result
    finally:
        await bus.cleanup()


async def test_success_and_business_rejection_break_the_failure_streak() -> None:
    registry = ToolRegistry(failure_threshold=2)
    calls = {"n": 0}

    async def flip(inv: ToolInvocation) -> ToolExecutionResult:
        calls["n"] += 1
        return await (_bad(inv) if calls["n"] % 2 == 1 else _ok(inv))  # 交替失败/成功

    registry.register(ToolSpec(name="flip", description="d", provider="p"), flip)
    for _ in range(3):
        await registry.invoke(ToolInvocation(tool_name="p_flip"))
    entry = _ops(registry).tool_health_snapshot()["p_flip"]
    assert not registry.is_tripped("p_flip") and (entry["state"], entry["failure_count"]) == ("healthy", 1)

    # 业务拒绝确认服务可应答：前后的传输故障不能拼成一次连续故障
    replies = iter(
        [
            ToolExecutionResult(tool_name="p_read", success=False, error_message="connection reset"),
            ToolExecutionResult(
                tool_name="p_read", success=False, error_message="invalid request", failure_kind="business"
            ),
            ToolExecutionResult(tool_name="p_read", success=False, error_message="connection reset"),
        ]
    )

    async def respond(invocation: ToolInvocation) -> ToolExecutionResult:
        return next(replies)

    registry.register(ToolSpec(name="read", description="", provider="p"), respond)
    for _ in range(3):
        assert not (await registry.invoke(ToolInvocation(tool_name="p_read"))).success
    assert not registry.is_tripped("p_read") and _ops(registry).tool_health_snapshot()["p_read"]["failure_count"] == 1


async def test_trip_and_recover_emit_open_and_closed_health_events() -> None:
    bus = EventBus(enable_stats=False)
    events: List[ToolHealthPayload] = []

    async def on_health(name: str, payload: ToolHealthPayload, source: str) -> None:
        events.append(payload)

    bus.on("tool.health.#", on_health, ToolHealthPayload)
    registry = _registry_with("z", event_bus=bus, failure_threshold=1)
    try:
        await registry.invoke(ToolInvocation(tool_name="p_z"))
        await _until(lambda: len(events) == 1)
        assert [(e.state, e.tool_name) for e in events] == [("open", "p_z")]
        registry.recover_tool("p_z")
        await _until(lambda: len(events) == 2)
        assert events[-1].state == "closed" and not registry.is_tripped("p_z")
    finally:
        await bus.cleanup()


async def test_threshold_zero_never_trips_but_records_failures_and_snapshot_lists_only_failed() -> None:
    registry = _registry_with("never", failure_threshold=0)
    registry.register(ToolSpec(name="clean", description="d", provider="p"), _ok)
    await registry.invoke(ToolInvocation(tool_name="p_clean"))
    assert _ops(registry).tool_health_snapshot() == {}  # 快照只含熔断中或有过失败的工具

    for _ in range(10):
        await registry.invoke(ToolInvocation(tool_name="p_never"))
    snap = _ops(registry).tool_health_snapshot()
    assert not registry.is_tripped("p_never") and set(snap) == {"p_never"}
    assert (snap["p_never"]["state"], snap["p_never"]["failure_count"]) == ("healthy", 10)

    await registry.invoke(ToolInvocation(tool_name="ghost"))  # 未知工具短路不触达健康状态
    assert "ghost" not in _ops(registry).tool_health_snapshot()


# 探活入口 probe_tool


async def test_probe_tool_follows_owner_health_check() -> None:
    registry = ToolRegistry()
    registry.register_provider(_Provider("default"))  # 基类默认探活：无可检查之物，让流量决定 → True
    mcp = _Provider("mcp", healthy=False)
    registry.register_provider(mcp)
    registry.register_provider(_Provider("boom", raises=True))
    registry.register(ToolSpec(name="lonely", description="d", provider="nowhere"), _ok)  # 直注册，无归属

    assert await registry.probe_tool("default_tool") is True
    assert await registry.probe_tool("mcp_tool") is False and mcp.calls == 1
    mcp._healthy = True
    assert await registry.probe_tool("mcp_tool") is True
    assert await registry.probe_tool("boom_tool") is False  # 探活抛异常按不健康处理，不外抛
    assert await registry.probe_tool("nowhere_lonely") is True and await registry.probe_tool("ghost") is True


async def test_register_non_base_provider_warns_and_has_no_probe_owner() -> None:
    class Legacy:  # duck-typed Provider，不继承 BaseToolProvider
        name = "Legacy"

        def list_tools(self):
            return [ToolSpec(name="ltool", description="d", kind="sync", provider="Legacy")]

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    registry = ToolRegistry()
    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(m), level="WARNING")
    try:
        registry.register_provider(Legacy())  # type: ignore[arg-type]
    finally:
        logger.remove(handler_id)
    assert any("非 BaseToolProvider 子类" in m for m in messages)
    assert await registry.probe_tool("Legacy_ltool") is True  # 未进所有权映射 → 无归属 → 默认健康


# 监控器 probe_cycle


@pytest.mark.parametrize(
    ("provider", "recovered"),
    [
        (_Provider(healthy=True), True),
        (_Provider(), True),  # 默认 health_check：探活即恢复
        (_Provider(healthy=False), False),
        (_Provider(raises=True), False),  # 探活抛异常视为不健康，继续熔断
    ],
)
async def test_probe_cycle_recovers_only_when_probe_passes(provider: _Provider, recovered: bool) -> None:
    registry = ToolRegistry()
    registry.register_provider(provider)
    _force_trip(registry, "probe_tool")
    await ToolHealthMonitor(registry, probe_interval_ms=10).probe_cycle()
    assert registry.is_tripped("probe_tool") is (not recovered)


async def test_probe_cycle_skips_tools_within_minimum_dwell() -> None:
    registry = _registry_with("dwell", provider="probe", failure_threshold=1)
    registry.register_provider(_Provider(healthy=True))
    await registry.invoke(ToolInvocation(tool_name="probe_dwell"))
    assert registry.is_tripped("probe_dwell")
    await ToolHealthMonitor(registry, probe_interval_ms=10_000).probe_cycle()  # 极长驻留
    assert registry.is_tripped("probe_dwell")  # 驻留未到，本轮不探活
