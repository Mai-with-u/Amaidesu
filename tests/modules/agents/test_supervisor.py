"""心跳任务与 Agent 守护（巡检 + 自动重建 + 风暴保护）

时间处理 fast-forward：心跳任务用极小间隔真实运行；判死不真等 60 秒，直接把 last_heartbeat_ms 拨到过去。
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Iterable, Optional

import pytest

from src.modules.agents import AgentManager, AgentState, BaseAgent, factory
from src.modules.agents.base import AgentHeartbeat
from src.modules.agents.control import AgentControl
from src.modules.config.core_schemas import AgentSupervisorConfig
from src.modules.tools.models import ToolSpec


class _SampleAgent(BaseAgent):
    """最小可工作子类；fail_next_start 置真时下一次 start 失败（模拟坏实例）。"""

    name = "sample_agent"
    description = "sample agent for supervisor tests"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fail_next_start = False

    def list_tools(self) -> Iterable[ToolSpec]:
        return []

    async def _on_start(self) -> None:
        if self.fail_next_start:
            self.fail_next_start = False
            raise RuntimeError("start_failed")

    async def _on_stop(self) -> None:
        return None


def _make_dead(agent: BaseAgent) -> None:
    agent._heartbeat = AgentHeartbeat(agent_name=agent.name, last_heartbeat_ms=1)  # noqa: SLF001


@pytest.fixture
async def manager(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AgentManager, None]:
    """带守护配置的 manager；工厂按名产出 _SampleAgent（绕过生产 Agent 的重依赖）。"""
    monkeypatch.setattr(
        factory, "instantiate_agent", lambda name, config, **kwargs: _SampleAgent() if name == "sample_agent" else None
    )
    config = AgentSupervisorConfig(
        check_interval_ms=50, dead_threshold_ms=1000, rebuild_failure_window_ms=600_000, max_rebuild_failures=3
    )
    mgr = AgentManager(supervisor_config=config)
    yield mgr
    await mgr.stop_supervisor()
    for name in mgr.list_agents():
        agent = mgr.get_agent_by_name(name)
        if agent is not None and agent.state in (AgentState.RUNNING, AgentState.PAUSED, AgentState.STARTING):
            await agent.stop()


async def _enable_sample(mgr: AgentManager) -> _SampleAgent:
    assert await mgr.enable_agent("sample_agent") is True
    agent = mgr.get_agent_by_name("sample_agent")
    assert isinstance(agent, _SampleAgent)
    return agent


async def test_heartbeat_task_follows_start_stop_and_can_be_disabled() -> None:
    agent = _SampleAgent(heartbeat_interval_ms=10)
    assert agent._heartbeat_task is None  # noqa: SLF001
    await agent.start()
    try:
        assert agent._heartbeat_task is not None and not agent._heartbeat_task.done()  # noqa: SLF001
        before = agent.heartbeat.last_heartbeat_ms
        await asyncio.sleep(0.06)  # ≥ 6 个间隔，心跳必然真实推进
        assert agent.heartbeat.last_heartbeat_ms > before
    finally:
        await agent.stop()
    assert agent._heartbeat_task is None  # noqa: SLF001

    off = _SampleAgent(heartbeat_interval_ms=0)  # <=0 → 心跳关闭，start 不创建任务
    await off.start()
    try:
        assert off._heartbeat_task is None  # noqa: SLF001
    finally:
        await off.stop()
    assert _SampleAgent()._heartbeat_interval_ms == AgentSupervisorConfig().heartbeat_interval_ms  # noqa: SLF001  默认值单一权威


async def test_supervisor_rebuilds_only_dead_live_agents(manager: AgentManager) -> None:
    healthy = await _enable_sample(manager)
    await manager._supervise_once()  # noqa: SLF001
    assert manager.get_agent_by_name("sample_agent") is healthy  # 空闲但心跳正常，不误判死

    _make_dead(healthy)
    await manager._supervise_once()  # noqa: SLF001  直调单轮巡检
    rebuilt = manager.get_agent_by_name("sample_agent")
    assert rebuilt is not healthy and rebuilt.state == AgentState.RUNNING
    assert rebuilt.restart_count == 1  # 重启计数跨实例继承并 +1

    await rebuilt.stop()
    _make_dead(rebuilt)
    await manager._supervise_once()  # noqa: SLF001
    assert manager.get_agent_by_name("sample_agent") is rebuilt  # STOPPED 等终态不在巡检范围


async def test_storm_protection_quarantines_after_the_failure_limit_and_manual_enable_resets_it(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await _enable_sample(manager)

    def failing_instantiate(name: str, config, **kwargs) -> Optional[BaseAgent]:
        bad = _SampleAgent()
        bad.fail_next_start = True  # 持续坏重建
        return bad if name == "sample_agent" else None

    monkeypatch.setattr(factory, "instantiate_agent", failing_instantiate)
    _make_dead(agent)

    for _ in range(2):  # 第 1、2 次失败仍在窗口内重试；每轮把 ERRORED 遗留实例的心跳拨死，模拟时间流逝
        await manager._supervise_once()  # noqa: SLF001
        assert "sample_agent" not in manager._supervisor_quarantined  # noqa: SLF001
        leftover = manager.get_agent_by_name("sample_agent")
        assert leftover is not None and leftover.state == AgentState.ERRORED
        _make_dead(leftover)

    await manager._supervise_once()  # noqa: SLF001  第 3 次失败：达上限 → 隔离，状态置 ERRORED
    current = manager.get_agent_by_name("sample_agent")
    assert "sample_agent" in manager._supervisor_quarantined and current.state == AgentState.ERRORED  # noqa: SLF001

    _make_dead(current)
    await manager._supervise_once()  # noqa: SLF001
    assert manager.get_agent_by_name("sample_agent") is current  # 风暴保护生效，心跳依然死也不再重试

    monkeypatch.setattr(factory, "instantiate_agent", lambda name, config, **kw: _SampleAgent())
    manager._rebuild_failures["sample_agent"] = [1, 2, 3]  # noqa: SLF001
    assert await manager.disable_agent("sample_agent") is True  # 人工先停用再启用，清除风暴记录
    await _enable_sample(manager)
    assert "sample_agent" not in manager._supervisor_quarantined  # noqa: SLF001
    assert "sample_agent" not in manager._rebuild_failures  # noqa: SLF001
    assert current.state == AgentState.STOPPED


async def test_observation_surface_reports_alive_heartbeat_and_restarts(manager: AgentManager) -> None:
    agent = await _enable_sample(manager)
    assert manager.is_agent_alive("sample_agent") is True and manager.is_agent_alive("nope") is None
    info = AgentControl(manager).state_of("sample_agent")
    assert info["heartbeat_ms"] == agent.heartbeat.last_heartbeat_ms > 0
    assert (info["is_alive"], info["restart_count"], info["state"]) == (True, 0, "running")

    _make_dead(agent)
    assert manager.is_agent_alive("sample_agent") is False  # 按守护配置阈值判活


async def test_supervisor_loop_start_stop_is_idempotent_and_off_when_interval_is_zero(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager.start_supervisor()
    task = manager._supervisor_task  # noqa: SLF001
    assert task is not None and not task.done()
    manager.start_supervisor()
    assert manager._supervisor_task is task  # noqa: SLF001  不重复创建
    await manager.stop_supervisor()
    assert manager._supervisor_task is None  # noqa: SLF001
    await manager.stop_supervisor()

    monkeypatch.setattr(factory, "instantiate_agent", lambda name, config, **kw: None)
    disabled = AgentManager(supervisor_config=AgentSupervisorConfig(check_interval_ms=0))
    try:
        disabled.start_supervisor()
        assert disabled._supervisor_task is None  # noqa: SLF001
    finally:
        await disabled.stop_supervisor()
