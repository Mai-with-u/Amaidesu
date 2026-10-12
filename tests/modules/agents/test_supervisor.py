"""心跳任务与 Agent 守护（巡检 + 自动重建 + 风暴保护）单元测试

时间处理采用 fast-forward 风格：心跳任务用极小间隔真实运行；判死不真等
60 秒，直接把 ``last_heartbeat_ms`` 拨到过去（与既有 test_base_agent.py
的时间处理惯例一致）。

覆盖：
- 心跳任务随 start 创建 / stop 取消（含真实推进心跳时间戳）
- 心跳停 → 巡检标死 → rebuild 一次、restart_count +1（跨实例继承）
- rebuild 持续失败 → 达上限置 ERRORED 且不再重试（防重启风暴）
- 空闲但心跳正常的 Agent 不被误判死
- state_of 观测面（heartbeat_ms / is_alive / restart_count）
"""

from __future__ import annotations

from typing import AsyncGenerator, Iterable, Optional

import asyncio

import pytest

from src.modules.agents import AgentManager, AgentState, BaseAgent
from src.modules.agents.base import AgentHeartbeat
from src.modules.agents.control import AgentControl
from src.modules.config.core_schemas import AgentSupervisorConfig
from src.modules.tools.models import ToolSpec


# =============================================================================
# 测试替身
# =============================================================================


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
    """fast-forward：把心跳拨到远古时刻（不真等 dead_threshold_ms）。"""
    agent._heartbeat = AgentHeartbeat(agent_name=agent.name, last_heartbeat_ms=1)  # noqa: SLF001


@pytest.fixture
def supervisor_cfg() -> AgentSupervisorConfig:
    return AgentSupervisorConfig(
        check_interval_ms=50,
        dead_threshold_ms=1000,
        rebuild_failure_window_ms=600_000,
        max_rebuild_failures=3,
    )


@pytest.fixture
async def manager(
    supervisor_cfg: AgentSupervisorConfig, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AgentManager, None]:
    """带守护配置的 manager；工厂按名产出 _SampleAgent（绕过生产三类 Agent 重依赖）。"""
    from src.modules.agents import factory

    def _fake_instantiate(name: str, config, **kwargs) -> Optional[BaseAgent]:
        if name != "sample_agent":
            return None
        return _SampleAgent()

    monkeypatch.setattr(factory, "instantiate_agent", _fake_instantiate)
    mgr = AgentManager(supervisor_config=supervisor_cfg)
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


# =============================================================================
# BaseAgent 心跳后台任务
# =============================================================================


async def test_heartbeat_task_created_on_start_and_cancelled_on_stop() -> None:
    """start 创建心跳任务并真实推进时间戳；stop 取消。"""
    agent = _SampleAgent(heartbeat_interval_ms=10)
    assert agent._heartbeat_task is None  # noqa: SLF001 - CREATED 下无任务
    await agent.start()
    try:
        assert agent._heartbeat_task is not None and not agent._heartbeat_task.done()  # noqa: SLF001
        before = agent.heartbeat.last_heartbeat_ms
        await asyncio.sleep(0.06)  # ≥ 6 个间隔，心跳必然真实推进
        assert agent.heartbeat.last_heartbeat_ms > before
    finally:
        await agent.stop()
    assert agent._heartbeat_task is None  # noqa: SLF001 - stop 后句柄清空


async def test_heartbeat_task_absent_when_disabled() -> None:
    """heartbeat_interval_ms<=0 → 心跳关闭，start 不创建任务。"""
    agent = _SampleAgent(heartbeat_interval_ms=0)
    await agent.start()
    try:
        assert agent._heartbeat_task is None  # noqa: SLF001
    finally:
        await agent.stop()


def test_heartbeat_interval_default_from_supervisor_config() -> None:
    """构造未传 interval → 默认值来自 AgentSupervisorConfig（单一权威）。"""
    agent = _SampleAgent()
    expected = AgentSupervisorConfig().heartbeat_interval_ms
    assert agent._heartbeat_interval_ms == expected  # noqa: SLF001


# =============================================================================
# 巡检：心跳停 → 标死 → 自动重建
# =============================================================================


async def test_supervisor_rebuilds_dead_agent_and_increments_restart_count(
    manager: AgentManager,
) -> None:
    """心跳停 → 单轮巡检判死 → rebuild 一次；新实例 restart_count == 1。"""
    agent = await _enable_sample(manager)
    _make_dead(agent)

    await manager._supervise_once()  # noqa: SLF001 - 直调单轮巡检（fast-forward）

    new_agent = manager.get_agent_by_name("sample_agent")
    assert new_agent is not None
    assert new_agent is not agent, "巡检后应替换为重建的新实例"
    assert new_agent.state == AgentState.RUNNING
    assert new_agent.restart_count == 1, "重启计数跨实例继承并 +1"


async def test_supervisor_skips_healthy_idle_agent(manager: AgentManager) -> None:
    """空闲但心跳正常（心跳任务在跑）的 Agent 不被误判死、不重建。"""
    agent = await _enable_sample(manager)
    await manager._supervise_once()  # noqa: SLF001
    assert manager.get_agent_by_name("sample_agent") is agent, "存活 Agent 不应被重建"
    assert agent.state == AgentState.RUNNING


async def test_supervisor_ignores_terminal_states(manager: AgentManager) -> None:
    """STOPPED 等终态 Agent 不在巡检范围（不误判、不重建）。"""
    agent = await _enable_sample(manager)
    await agent.stop()
    _make_dead(agent)
    await manager._supervise_once()  # noqa: SLF001
    assert manager.get_agent_by_name("sample_agent") is agent


# =============================================================================
# 防重启风暴：窗口内连续失败达上限 → ERRORED + 停止重试
# =============================================================================


async def test_storm_protection_stops_retrying_after_failure_limit(
    manager: AgentManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rebuild 持续失败 → 3 次失败后置 ERRORED，后续巡检不再尝试重建。"""
    agent = await _enable_sample(manager)

    # 后续工厂产出的实例 start 必失败（模拟持续坏重建）
    def _failing_instantiate(name: str, config, **kwargs) -> Optional[BaseAgent]:
        if name != "sample_agent":
            return None
        bad = _SampleAgent()
        bad.fail_next_start = True
        return bad

    from src.modules.agents import factory

    monkeypatch.setattr(factory, "instantiate_agent", _failing_instantiate)

    _make_dead(agent)
    rebuild_calls = 0

    # 第 1、2 次失败：仍在窗口内重试（每轮把 ERRORED 遗留实例的心跳拨死，
    # 模拟时间流逝超过判死阈值）
    for _ in range(2):
        await manager._supervise_once()  # noqa: SLF001
        rebuild_calls += 1
        assert "sample_agent" not in manager._supervisor_quarantined  # noqa: SLF001
        leftover = manager.get_agent_by_name("sample_agent")
        assert leftover is not None and leftover.state == AgentState.ERRORED
        _make_dead(leftover)

    # 第 3 次失败：达上限 → 隔离 + 状态置 ERRORED
    await manager._supervise_once()  # noqa: SLF001
    rebuild_calls += 1
    assert "sample_agent" in manager._supervisor_quarantined  # noqa: SLF001
    current = manager.get_agent_by_name("sample_agent")
    assert current is not None and current.state == AgentState.ERRORED

    # 第 4 轮：不再重建（心跳依然死也不重试）
    if current is not None:
        _make_dead(current)
    await manager._supervise_once()  # noqa: SLF001
    assert manager.get_agent_by_name("sample_agent") is current, "风暴保护生效，实例不再被替换"
    assert rebuild_calls == 3, "重建尝试恰好 3 次（= max_rebuild_failures）"


async def test_manual_enable_resets_storm_protection(manager: AgentManager) -> None:
    """达到失败上限后，人工重新 enable 清除风暴记录（恢复重建资格）。"""
    agent = await _enable_sample(manager)
    manager._supervisor_quarantined.add("sample_agent")  # noqa: SLF001 - 直接模拟达限状态
    manager._rebuild_failures["sample_agent"] = [1, 2, 3]  # noqa: SLF001

    # 人工介入：先停用再重新启用（dashboard 组件管理同一路径）
    assert await manager.disable_agent("sample_agent") is True
    await _enable_sample(manager)

    assert "sample_agent" not in manager._supervisor_quarantined  # noqa: SLF001
    assert "sample_agent" not in manager._rebuild_failures  # noqa: SLF001
    assert agent.state == AgentState.STOPPED  # 旧实例已被 disable 停止


# =============================================================================
# 观测面：is_agent_alive / state_of
# =============================================================================


async def test_is_agent_alive_uses_supervisor_threshold(manager: AgentManager) -> None:
    """manager.is_agent_alive 按守护配置阈值判活；未注册名返回 None。"""
    agent = await _enable_sample(manager)
    assert manager.is_agent_alive("sample_agent") is True
    assert manager.is_agent_alive("nope") is None
    _make_dead(agent)
    assert manager.is_agent_alive("sample_agent") is False


async def test_state_of_exposes_heartbeat_alive_restart(manager: AgentManager) -> None:
    """state_of 暴露 heartbeat_ms / is_alive / restart_count。"""
    agent = await _enable_sample(manager)
    control = AgentControl(manager)
    info = control.state_of("sample_agent")
    assert info is not None
    assert info["heartbeat_ms"] == agent.heartbeat.last_heartbeat_ms > 0
    assert info["is_alive"] is True
    assert info["restart_count"] == 0
    assert info["state"] == "running"


# =============================================================================
# 守护循环启停
# =============================================================================


async def test_supervisor_loop_start_stop(manager: AgentManager) -> None:
    """start_supervisor 创建任务；stop_supervisor 取消；均幂等。"""
    manager.start_supervisor()
    task = manager._supervisor_task  # noqa: SLF001
    assert task is not None and not task.done()
    manager.start_supervisor()  # 幂等：不重复创建
    assert manager._supervisor_task is task  # noqa: SLF001
    await manager.stop_supervisor()
    assert manager._supervisor_task is None  # noqa: SLF001
    await manager.stop_supervisor()  # 幂等


async def test_supervisor_loop_disabled_when_interval_zero(
    supervisor_cfg: AgentSupervisorConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_interval_ms<=0 → 巡检关闭，不创建任务。"""
    from src.modules.agents import factory

    monkeypatch.setattr(factory, "instantiate_agent", lambda name, config, **kw: None)
    cfg = AgentSupervisorConfig(check_interval_ms=0)
    mgr = AgentManager(supervisor_config=cfg)
    try:
        mgr.start_supervisor()
        assert mgr._supervisor_task is None  # noqa: SLF001
    finally:
        await mgr.stop_supervisor()
