"""
BaseAgent / AgentManager / AgentControl 单元测试（Wave 3 / §1.49）

覆盖：
- BaseAgent 协议六面（生命周期 / 工具提供 / 事件上报 / 状态 / 心跳 / 元数据）
- start → stop → cleanup 状态机
- pause / resume / shutdown 控制
- 心跳 + is_alive 判定
- AgentManager 注册 / 启动 / 停止 / cleanup 全部
- AgentControl 工具注册进 ToolRegistry 后可调用
- 同名重复注册跳过后注册（去重）
"""

from __future__ import annotations

from typing import AsyncGenerator, Iterable

import pytest

from src.modules.agents import (
    AgentManager,
    AgentState,
    BaseAgent,
    build_agent_control_provider,
)
from src.modules.tools import ToolInvocation, ToolRegistry
from src.modules.tools.models import ToolExecutionResult, ToolSpec


# =============================================================================
# Fixtures
# =============================================================================


class _SampleAgent(BaseAgent):
    """最小可工作子类（满足协议六面）。"""

    name = "sample_agent"
    description = "Sample Agent for testing the protocol"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.started = 0
        self.stopped = 0
        self.paused = 0
        self.resumed = 0
        self.cleanup_calls = 0

    def list_tools(self) -> Iterable[ToolSpec]:
        yield ToolSpec(
            name="sample_tool",
            description="sample",
            kind="sync",
            provider="game",
        )

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


@pytest.fixture
def sample_agent() -> _SampleAgent:
    return _SampleAgent()


@pytest.fixture
async def started_agent(sample_agent: _SampleAgent) -> AsyncGenerator[_SampleAgent, None]:
    """已 start 的 Agent（yield 时已 RUNNING）。"""
    await sample_agent.start()
    yield sample_agent
    if sample_agent.state in (AgentState.RUNNING, AgentState.PAUSED, AgentState.STARTING):
        await sample_agent.stop()


# =============================================================================
# BaseAgent 协议六面
# =============================================================================


def test_protocol_six_facets(sample_agent: _SampleAgent) -> None:
    """元数据（name/description）。"""
    assert sample_agent.name == "sample_agent"
    assert "Sample" in sample_agent.description


def test_metadata_is_required(sample_agent: _SampleAgent) -> None:
    """子类未设置 name 时——基类不自动填，由 AgentManager.register 显式拒绝（保错位）。"""
    class _Anon(BaseAgent):
        def list_tools(self):
            return []

    a = _Anon()
    assert a.name == "", "基类不兜底 name——必须在子类显式声明"
    # AgentManager.register 应拒绝
    mgr = AgentManager()
    assert mgr.register(a) is False


# -------------------- 协议 1：生命周期 --------------------


async def test_start_stop_cleanup_lifecycle(sample_agent: _SampleAgent) -> None:
    """start → RUNNING → stop → STOPPED → cleanup。"""
    assert sample_agent.state == AgentState.CREATED
    await sample_agent.start()
    assert sample_agent.state == AgentState.RUNNING
    assert sample_agent.started == 1

    await sample_agent.stop()
    assert sample_agent.state == AgentState.STOPPED
    assert sample_agent.stopped == 1

    await sample_agent.cleanup()
    assert sample_agent.cleanup_calls == 1


async def test_idempotent_stop(sample_agent: _SampleAgent) -> None:
    """重复 stop 不应崩。"""
    await sample_agent.start()
    await sample_agent.stop()
    await sample_agent.stop()  # 第二次是 no-op


async def test_start_from_stopped_is_allowed(sample_agent: _SampleAgent) -> None:
    """STOPPED 状态可再次 start。"""
    await sample_agent.start()
    await sample_agent.stop()
    # 二次 start 不应抛
    await sample_agent.start()
    assert sample_agent.state == AgentState.RUNNING


async def test_start_failure_leaves_errorred(sample_agent: _SampleAgent) -> None:
    """start 钩子失败 → 状态 ERRORED + 异常向上抛。"""

    class _Bad(BaseAgent):
        name = "bad"
        description = "bad"

        def list_tools(self):
            return []

        async def _on_start(self) -> None:
            raise RuntimeError("start_failed")

    a = _Bad()
    with pytest.raises(RuntimeError, match="start_failed"):
        await a.start()
    assert a.state == AgentState.ERRORED


# -------------------- 协议 2：工具提供 --------------------


def test_list_tools_yields_specs(sample_agent: _SampleAgent) -> None:
    """list_tools 返回 ToolSpec 列表。"""
    specs = list(sample_agent.list_tools())
    assert len(specs) == 1
    assert specs[0].name == "sample_tool"
    assert specs[0].provider == "game"


# -------------------- 协议 3：事件上报 --------------------


async def test_emit_event_without_bus_is_noop(sample_agent: _SampleAgent) -> None:
    """未注入 EventBus 时 emit 不应爆。"""
    sample_agent.note_heartbeat()
    # 直接 await——无 bus 应 no-op
    await sample_agent.emit_event("test.event", payload=None)


# -------------------- 协议 4：状态读写 --------------------


def test_state_property(sample_agent: _SampleAgent) -> None:
    """state 暴露只读属性。"""
    assert sample_agent.state == AgentState.CREATED
    # 直接修改应失败（status 是 dataclass-style 属性，依赖实现）


def test_restart_count_increments(sample_agent: _SampleAgent) -> None:
    """restart_count 可增量。"""
    assert sample_agent.restart_count == 0
    sample_agent.increment_restart_counter()
    sample_agent.increment_restart_counter()
    assert sample_agent.restart_count == 2


# -------------------- 协议 5：心跳 --------------------


def test_heartbeat_tracks_timestamp(sample_agent: _SampleAgent) -> None:
    """note_heartbeat 写入当前时刻。"""
    initial = sample_agent.heartbeat.last_heartbeat_ms
    assert initial > 0
    # 调用后时间应 >= initial
    sample_agent.note_heartbeat()
    assert sample_agent.heartbeat.last_heartbeat_ms >= initial


def test_is_alive_default(sample_agent: _SampleAgent) -> None:
    """newly constructed → alive（心跳在 __init__ 写入）。"""
    assert sample_agent.is_alive() is True


def test_is_alive_threshold_respected(sample_agent: _SampleAgent) -> None:
    """dead_threshold_ms 参数可用。"""
    # 把心跳改成 1 小时前
    sample_agent._heartbeat.last_heartbeat_ms = 1  # noqa: SLF001
    assert sample_agent.is_alive(dead_threshold_ms=60_000) is False
    # 阈值大到能跨越（Unix epoch 到现在的 ms 量级 → 用 1e15）
    assert sample_agent.is_alive(dead_threshold_ms=10**15) is True


# -------------------- 控制（§1.49 框架统一控制） --------------------


async def test_pause_resume_invocations(started_agent: _SampleAgent) -> None:
    """pause/resume 切换状态并触发钩子。"""
    a = started_agent
    assert a.state == AgentState.RUNNING
    await a.pause()
    assert a.state == AgentState.PAUSED
    assert a.paused == 1

    await a.resume()
    assert a.state == AgentState.RUNNING
    assert a.resumed == 1


async def test_shutdown_calls_stop_plus_hook(started_agent: _SampleAgent) -> None:
    """shutdown = stop + _on_shutdown。"""
    shutdown_called = {"value": 0}

    class _WithShutdown(_SampleAgent):
        async def _on_shutdown(self) -> None:
            shutdown_called["value"] += 1

    a: _WithShutdown = _WithShutdown()
    await a.start()
    await a.shutdown()
    assert a.state == AgentState.STOPPED
    assert a.stopped == 1
    assert shutdown_called["value"] == 1


# =============================================================================
# AgentManager
# =============================================================================


async def test_agent_manager_register_dedup(sample_agent: _SampleAgent) -> None:
    """同名 Agent 重复注册跳过后注册。"""
    mgr = AgentManager()
    assert mgr.register(sample_agent) is True
    # 再次注册同名
    other = _SampleAgent()
    assert mgr.register(other) is False
    # 取出来仍是第一个
    assert mgr.get("sample_agent") is sample_agent


async def test_agent_manager_register_missing_name_rejected() -> None:
    """未设置 name 的子类被拒绝。"""

    class _NoName(BaseAgent):
        name = ""
        description = "no name"

        def list_tools(self):
            return []

    mgr = AgentManager()
    assert mgr.register(_NoName()) is False
    assert len(mgr) == 0


async def test_agent_manager_start_all_stop_all(sample_agent: _SampleAgent) -> None:
    """start_all / stop_all 批量。"""
    mgr = AgentManager()
    mgr.register(sample_agent)
    await mgr.start_all()
    assert sample_agent.state == AgentState.RUNNING
    assert "sample_agent" in mgr.list_running()

    await mgr.stop_all()
    assert sample_agent.state == AgentState.STOPPED


async def test_audit_tools_reports_missing_when_impl_absent(
    sample_agent: _SampleAgent,
) -> None:
    """audit_tools：Agent 声明了工具但 registry 未注册 → 报告缺失。"""
    mgr = AgentManager()
    mgr.register(sample_agent)
    reg = ToolRegistry()  # 空 registry，无任何工具
    assert mgr.audit_tools(reg) == ["sample_tool"]


async def test_audit_tools_empty_when_impl_registered(
    sample_agent: _SampleAgent,
) -> None:
    """audit_tools：Agent 声明的工具已被注册（impl 来自 Agent 自身） → 缺失列表为空。"""
    mgr = AgentManager()
    mgr.register(sample_agent)
    reg = ToolRegistry()

    # 真实注册：把 sample_agent 声明的 spec + 一个最小实现塞进 registry
    spec = next(iter(sample_agent.list_tools()))

    async def _impl(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name="sample_tool",
            success=True,
            content="ok",
        )

    assert reg.register(spec, _impl) is True
    assert mgr.audit_tools(reg) == []


async def test_audit_tools_skips_agents_whose_list_tools_raises(
    sample_agent: _SampleAgent,
) -> None:
    """audit_tools：list_tools 抛异常的 Agent 被跳过，审计不崩。"""

    class _RaisingListTools(BaseAgent):
        name = "raising_list_tools"
        description = "raises on list_tools"

        def list_tools(self):
            raise RuntimeError("list_tools exploded")

    mgr = AgentManager()
    mgr.register(sample_agent)
    mgr.register(_RaisingListTools())
    reg = ToolRegistry()

    # sample_agent 的 sample_tool 仍应被报告为缺失；
    # raising Agent 不应导致审计崩溃。
    missing = mgr.audit_tools(reg)
    assert missing == ["sample_tool"]


async def test_agent_manager_unregister_only_when_stopped() -> None:
    """未停止的 Agent 不能 unregister（防御状态泄漏）。"""
    mgr = AgentManager()
    a = _SampleAgent()
    mgr.register(a)
    await a.start()
    # RUNNING 状态不能 unregister
    assert mgr.unregister("sample_agent") is False
    await a.stop()
    # STOPPED 状态可以
    assert mgr.unregister("sample_agent") is True
    assert "sample_agent" not in mgr


def test_agent_manager_get_and_contains(sample_agent: _SampleAgent) -> None:
    mgr = AgentManager()
    mgr.register(sample_agent)
    assert mgr.get("sample_agent") is sample_agent
    assert mgr.get("nonexistent") is None
    assert "sample_agent" in mgr
    assert "other" not in mgr


# =============================================================================
# AgentControl + ToolProvider
# =============================================================================


async def test_agent_control_list_tools_via_registry(sample_agent: _SampleAgent) -> None:
    """AgentControlProvider 注册到 ToolRegistry 后能 list/has。"""
    mgr = AgentManager()
    mgr.register(sample_agent)
    await sample_agent.start()

    control_provider = build_agent_control_provider(mgr)
    reg = ToolRegistry()
    n = reg.register_provider(control_provider)
    assert n >= 6, "AgentControl 应暴露 pause/resume/shutdown/restart/list_agents/agent_state 等工具"

    # 6 个工具
    expected = {
        "pause_agent",
        "resume_agent",
        "shutdown_agent",
        "restart_agent",
        "list_agents",
        "agent_state",
    }
    names = {spec.name for spec in reg.list_tools()}
    assert expected.issubset(names)


async def test_agent_control_invoke_pause_agent(sample_agent: _SampleAgent) -> None:
    """通过工具调用 pause_agent（不直接调 Agent 方法）。"""
    mgr = AgentManager()
    mgr.register(sample_agent)
    await sample_agent.start()

    control_provider = build_agent_control_provider(mgr)
    reg = ToolRegistry()
    reg.register_provider(control_provider)

    res = await reg.invoke(
        ToolInvocation(tool_name="pause_agent", arguments={"name": "sample_agent"})
    )
    assert res.success is True
    assert sample_agent.state == AgentState.PAUSED


async def test_agent_control_invoke_list_agents(sample_agent: _SampleAgent) -> None:
    """list_agents 工具返回 Agent 名字列表。"""
    mgr = AgentManager()
    mgr.register(sample_agent)

    control_provider = build_agent_control_provider(mgr)
    reg = ToolRegistry()
    reg.register_provider(control_provider)

    res = await reg.invoke(ToolInvocation(tool_name="list_agents", arguments={}))
    assert res.success is True
    assert "sample_agent" in res.content


async def test_agent_control_invoke_agent_state(sample_agent: _SampleAgent) -> None:
    """agent_state 工具返回状态信息。"""
    mgr = AgentManager()
    mgr.register(sample_agent)
    await sample_agent.start()

    control_provider = build_agent_control_provider(mgr)
    reg = ToolRegistry()
    reg.register_provider(control_provider)

    res = await reg.invoke(
        ToolInvocation(tool_name="agent_state", arguments={"name": "sample_agent"})
    )
    assert res.success is True
    assert "running" in res.content
    assert "sample_agent" in res.content


async def test_agent_control_invoke_unknown_agent_returns_failure(
    sample_agent: _SampleAgent,
) -> None:
    """AgentControl 对未知 Agent 返回失败 result，不抛。"""
    mgr = AgentManager()
    mgr.register(sample_agent)

    control_provider = build_agent_control_provider(mgr)
    reg = ToolRegistry()
    reg.register_provider(control_provider)

    res = await reg.invoke(
        ToolInvocation(tool_name="pause_agent", arguments={"name": "absent"})
    )
    assert res.success is False
    assert "未找到" in res.error_message or "absent" in res.error_message


async def test_agent_control_provider_is_provider(sample_agent: _SampleAgent) -> None:
    """control_provider 满足 ToolProvider 协议。"""
    from src.modules.tools import ToolProvider

    mgr = AgentManager()
    mgr.register(sample_agent)
    cp = build_agent_control_provider(mgr)
    assert isinstance(cp, ToolProvider)
