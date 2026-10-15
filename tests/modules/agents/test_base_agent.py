"""BaseAgent / AgentManager / AgentControl：生命周期状态机、心跳、框架统一控制、名册与工具审计、task.changed 唤醒"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Iterable, Optional

import pytest

from src.modules.agents import AgentManager, AgentState, BaseAgent, build_agent_control_provider
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.tools import ToolInvocation, ToolProvider, ToolRegistry
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.tasks import TaskLedger


class _SampleAgent(BaseAgent):
    name = "sample_agent"
    description = "Sample Agent for testing the protocol"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.started = self.stopped = self.paused = self.resumed = self.cleanup_calls = 0

    def list_tools(self) -> Iterable[ToolSpec]:
        yield ToolSpec(name="sample_tool", description="sample", kind="sync", provider="game")

    async def _on_start(self) -> None:
        self.started += 1

    async def _on_stop(self) -> None:
        self.stopped += 1

    async def _on_cleanup(self) -> None:
        self.cleanup_calls += 1

    async def _on_pause(self) -> None:
        self.paused += 1

    async def _on_resume(self) -> None:
        self.resumed += 1


class _ReceivingAgent(BaseAgent):
    """覆写委派/递话接入入口的最小子类——名册能力探测的"已接入"形态。"""

    name = "receiving_agent"
    description = "Accepts delegation and prompt"

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    def receive_delegation(self, *, instruction: str, task_id: str) -> Optional[str]:
        return None

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        return True


@pytest.fixture
def sample_agent() -> _SampleAgent:
    return _SampleAgent()


@pytest.fixture
async def started_agent(sample_agent: _SampleAgent) -> AsyncGenerator[_SampleAgent, None]:
    await sample_agent.start()
    yield sample_agent
    if sample_agent.state in (AgentState.RUNNING, AgentState.PAUSED, AgentState.STARTING):
        await sample_agent.stop()


async def test_lifecycle_state_machine(sample_agent: _SampleAgent) -> None:
    assert (sample_agent.name, sample_agent.state) == ("sample_agent", AgentState.CREATED)
    await sample_agent.start()
    assert (sample_agent.state, sample_agent.started) == (AgentState.RUNNING, 1)
    await sample_agent.stop()
    await sample_agent.stop()  # 重复 stop 是 no-op
    assert (sample_agent.state, sample_agent.stopped) == (AgentState.STOPPED, 1)
    await sample_agent.cleanup()
    assert sample_agent.cleanup_calls == 1
    await sample_agent.start()  # STOPPED 可再次 start
    assert sample_agent.state == AgentState.RUNNING


async def test_start_hook_failure_leaves_errored_and_reraises() -> None:
    class _Bad(BaseAgent):
        name = "bad"
        description = "bad"

        def list_tools(self):
            return []

        async def _on_start(self) -> None:
            raise RuntimeError("start_failed")

    agent = _Bad()
    with pytest.raises(RuntimeError, match="start_failed"):
        await agent.start()
    assert agent.state == AgentState.ERRORED


async def test_pause_resume_and_illegal_transitions(sample_agent: _SampleAgent) -> None:
    """非法转移一律拒绝：状态不变、钩子不触发（与 start/stop 守卫同一策略）。"""
    await sample_agent.pause()
    await sample_agent.resume()
    assert (sample_agent.state, sample_agent.paused, sample_agent.resumed) == (AgentState.CREATED, 0, 0)

    await sample_agent.start()
    await sample_agent.resume()  # RUNNING 下 resume 拒绝
    assert (sample_agent.state, sample_agent.resumed) == (AgentState.RUNNING, 0)
    await sample_agent.pause()
    assert (sample_agent.state, sample_agent.paused) == (AgentState.PAUSED, 1)
    await sample_agent.resume()
    assert (sample_agent.state, sample_agent.resumed) == (AgentState.RUNNING, 1)

    await sample_agent.stop()
    await sample_agent.pause()  # STOPPED 下 pause 拒绝
    assert (sample_agent.state, sample_agent.paused) == (AgentState.STOPPED, 1)


async def test_shutdown_is_stop_plus_hook() -> None:
    class _WithShutdown(_SampleAgent):
        shutdowns = 0

        async def _on_shutdown(self) -> None:
            self.shutdowns += 1

    agent = _WithShutdown()
    await agent.start()
    await agent.shutdown()
    assert (agent.state, agent.stopped, agent.shutdowns) == (AgentState.STOPPED, 1, 1)


def test_heartbeat_restart_counter_and_default_refusals(sample_agent: _SampleAgent) -> None:
    initial = sample_agent.heartbeat.last_heartbeat_ms
    assert initial > 0 and sample_agent.is_alive() is True  # 心跳在 __init__ 写入
    sample_agent.note_heartbeat()
    assert sample_agent.heartbeat.last_heartbeat_ms >= initial

    sample_agent._heartbeat.last_heartbeat_ms = 1
    assert sample_agent.is_alive(dead_threshold_ms=60_000) is False
    assert sample_agent.is_alive(dead_threshold_ms=10**15) is True

    sample_agent.increment_restart_counter()
    sample_agent.increment_restart_counter()
    assert sample_agent.restart_count == 2

    specs = list(sample_agent.list_tools())
    assert [(s.name, s.provider) for s in specs] == [("sample_tool", "game")]
    # 未实现递话消化 / 取消入口的子类走基类默认拒收，不抛异常
    assert sample_agent.receive_prompt(content="插句话", source="operator") is False
    assert sample_agent.cancel_task("t-nonexistent", source="operator") is False


async def test_emit_event_without_bus_is_noop(sample_agent: _SampleAgent) -> None:
    await sample_agent.emit_event("test.event", payload=None)


async def test_manager_registration_rules_and_batch_control(sample_agent: _SampleAgent) -> None:
    class _Anon(BaseAgent):
        def list_tools(self):
            return []

    anon = _Anon()
    mgr = AgentManager()
    # 基类不兜底 name，由 register 显式拒绝
    assert anon.name == "" and mgr.register(anon) is False and len(mgr) == 0

    assert mgr.register(sample_agent) is True
    assert mgr.register(_SampleAgent()) is False  # 同名重复注册跳过
    assert mgr.get_agent_by_name("sample_agent") is sample_agent and mgr.get_agent_by_name("nonexistent") is None
    assert "sample_agent" in mgr and "other" not in mgr

    await mgr.start_all()
    assert sample_agent.state == AgentState.RUNNING and "sample_agent" in mgr.list_running()
    assert mgr.unregister("sample_agent") is False  # 未停止的不能 unregister（防状态泄漏）
    await mgr.stop_all()
    assert sample_agent.state == AgentState.STOPPED
    assert mgr.unregister("sample_agent") is True and "sample_agent" not in mgr


async def test_audit_tools_reports_missing_and_survives_broken_agents(sample_agent: _SampleAgent) -> None:
    class _RaisingListTools(BaseAgent):
        name = "raising_list_tools"
        description = "raises on list_tools"

        def list_tools(self):
            raise RuntimeError("list_tools exploded")

    mgr = AgentManager()
    mgr.register(sample_agent)
    mgr.register(_RaisingListTools())  # list_tools 抛异常的 Agent 被跳过，审计不崩
    reg = ToolRegistry()
    assert mgr.audit_tools(reg) == ["game_sample_tool"]

    async def impl(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(tool_name="sample_tool", success=True, content="ok")

    assert reg.register(next(iter(sample_agent.list_tools())), impl) is True
    assert mgr.audit_tools(reg) == []


async def test_agent_control_provider_exposes_roster_only_for_receiving_agents(sample_agent: _SampleAgent) -> None:
    mgr = AgentManager()
    mgr.register(sample_agent)  # 未覆写接收入口 → 不进任何名册
    bare = build_agent_control_provider(mgr)
    assert isinstance(bare, ToolProvider)
    assert {s.name for s in bare.list_tools()} == {"delegate", "prompt", "task_status"}
    assert (
        "enum"
        not in next(s for s in bare.list_tools() if s.name == "delegate").parameters_schema["properties"]["agent"]
    )

    mgr.register(_ReceivingAgent())
    await sample_agent.start()
    provider = build_agent_control_provider(mgr)
    reg = ToolRegistry()
    assert reg.register_provider(provider) == 3

    # 6 个控制工具已移出 LLM 工具面（控制面经 DashboardServer 直调 AgentControl）
    removed = {
        f"framework_{n}"
        for n in ("pause_agent", "resume_agent", "shutdown_agent", "restart_agent", "list_agents", "agent_state")
    }
    names = {spec.full_name for spec in reg.list_tools(for_agent="streamer")}
    assert {"framework_delegate", "framework_prompt", "framework_task_status"} <= names and removed.isdisjoint(names)
    for tool in ("delegate", "prompt"):
        spec = next(s for s in provider.list_tools() if s.name == tool)
        assert spec.parameters_schema["properties"]["agent"]["enum"] == ["receiving_agent"]

    gone = await reg.invoke(ToolInvocation(tool_name="framework_pause_agent", arguments={"name": "sample_agent"}))
    assert gone.success is False and "未知" in gone.error_message
    assert sample_agent.state == AgentState.RUNNING  # 对已移出的控制工具 invoke 只失败，不抛、不生效


async def test_delegate_to_unknown_agent_fails_without_raising(sample_agent: _SampleAgent) -> None:
    mgr = AgentManager()
    mgr.register(sample_agent)
    mgr.register(_ReceivingAgent())  # 让 delegate 工具进入工具面（名册非空）
    reg = ToolRegistry()
    reg.register_provider(build_agent_control_provider(mgr, task_ledger=TaskLedger()))

    res = await reg.invoke(
        ToolInvocation(tool_name="framework_delegate", arguments={"agent": "absent", "instruction": "干活"})
    )
    assert res.success is False and "absent" in res.error_message


async def test_task_changed_wakes_only_the_initiator_and_stop_unsubscribes() -> None:
    class _WakeupAgent(_SampleAgent):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.notified: list[str] = []

        def on_task_notification(self, payload) -> None:
            self.notified.append(payload.task_id)

    bus = EventBus(enable_stats=False)
    agent = _WakeupAgent(event_bus=bus)
    await agent.start()

    async def fire(initiator: str, task_id: str) -> None:
        payload = TaskChangedPayload(task_id=task_id, status="running", initiator=initiator, executor="minecraft")
        await bus.emit(CoreEvents.TASK_CHANGED, payload, source="TaskLedger")

    try:
        await fire("sample_agent", "t-mine")
        await fire("other_agent", "t-other")  # 发起方是别人 → 过滤
        await asyncio.sleep(0.05)
        assert agent.notified == ["t-mine"]
    finally:
        await agent.stop()

    await fire("sample_agent", "t-after")  # stop 后退订，不再派发
    await asyncio.sleep(0.05)
    assert agent.notified == ["t-mine"]
    await bus.cleanup()
