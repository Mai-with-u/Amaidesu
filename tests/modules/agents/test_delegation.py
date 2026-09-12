"""委派原语集成测试（framework_delegate / framework_task_status，ADR-013）。

覆盖：
- 委派全程：回执 accepted+task_id → 状态推进（task.changed 按发起方过滤）→
  task_status 查询 → 终态移除
- 受理失败与任务失败分开：目标不存在 / 默认拒收（未实现入口）/ 自派 →
  受理失败（不登记）；执行失败 → 记录表 failed 终态
- 未知任务号查询 → 失败结果
- minecraft 接收委派（入队带任务号 + 唤醒 + 批次状态写回）
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from src.modules.agents.base import BaseAgent
from src.modules.agents.control import build_agent_control_provider
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker


def _inv(tool: str, args: dict, source: str = "agent_a") -> ToolInvocation:
    return ToolInvocation(tool_name=tool, arguments=args, source=source)


class _StubAgent(BaseAgent):
    """最小 Agent 桩：可配置是否接收委派、是否模拟执行失败。"""

    name = ""
    description = "stub"

    def __init__(self, name: str, *, refuse: str | None = None, fail_later: bool = False) -> None:
        # 动态子类隔离 name（BaseAgent 的 name 是类属性；同类多实例会互相覆盖）
        cls = type(f"_StubAgent_{name}", (type(self),), {"name": name, "description": f"stub {name}"})
        self.__class__ = cls
        super().__init__()
        self._refuse = refuse
        self._fail_later = fail_later
        self.received: List[tuple[str, str]] = []  # (task_id, instruction)

    def list_tools(self):
        return []

    def receive_delegation(self, *, instruction: str, task_id: str):
        if self._refuse:
            return self._refuse
        self.received.append((task_id, instruction))
        return None

    async def run_and_finish(self, ledger: TaskLedger, *, failed: bool = False) -> None:
        """测试驱动：把收到的委派任务推进到终态（写回记录表）。"""
        for task_id, _ in self.received:
            ledger.update(task_id, "running", summary="开始执行")
            ledger.update(
                task_id,
                "failed" if failed else "succeeded",
                summary="执行失败" if failed else "全部完成",
            )


def _setup(refuse_target: bool = False, fail_target: bool = False) -> tuple[ToolRegistry, TaskLedger, EventBus, _StubAgent, _StubAgent]:
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus)
    manager = AgentManager()
    a = _StubAgent("agent_a")
    b = _StubAgent("agent_b", refuse="忙不过来" if refuse_target else None, fail_later=fail_target)
    manager.register(a)
    manager.register(b)
    registry.register_provider(build_agent_control_provider(manager, ledger))
    return registry, ledger, bus, a, b


async def _drain() -> None:
    await asyncio.sleep(0.03)


# ---------------------------------------------------------------------------
# 委派全程（happy path）
# ---------------------------------------------------------------------------


async def test_delegation_full_chain() -> None:
    """A 委派 B → 回执 task_id → B 推进状态 → A 收 task.changed → 查询终态。"""
    registry, ledger, bus, a, b = _setup()
    received_by_a: List[TaskChangedPayload] = []

    async def _on_a(name: str, payload: TaskChangedPayload, source: str) -> None:
        received_by_a.append(payload)

    # A 订阅 task.changed（BaseAgent 默认行为在 start()；此处直接订阅验证过滤）
    bus.on(CoreEvents.TASK_CHANGED, _on_a, model_class=TaskChangedPayload)

    # 1. 委派 → 受理回执
    res = await registry.invoke(_inv("framework_delegate", {"agent": "agent_b", "instruction": "盖一座房子"}))
    assert res.success is True
    task_id = res.structured_content["task_id"]
    assert res.structured_content["accepted"] is True
    assert res.structured_content["executor"] == "agent_b"
    assert b.received == [(task_id, "盖一座房子")], "目标已接收（指令 + 任务号）"

    # 2. 记录表登记（agent 型）
    record = ledger.get(task_id)
    assert record is not None and record.initiator == "agent_a" and record.executor == "agent_b"
    assert record.source == "agent"

    # 3. B 推进 [running → succeeded]
    await b.run_and_finish(ledger)
    await _drain()

    # 4. A 收到状态变化事件（按发起方过滤在本测试由 BaseAgent 行为覆盖；此处验证事件内容）
    statuses = [p.status for p in received_by_a if p.task_id == task_id]
    # accepted→running 属安静组（受理转进行中不唤醒，A 已持回执）；终态必达
    assert statuses == ["succeeded"]
    assert all(p.initiator == "agent_a" and p.executor == "agent_b" for p in received_by_a)

    # 5. task_status 查询（终态已移除 → 任务不存在）
    q = await registry.invoke(_inv("framework_task_status", {"task_id": task_id}))
    assert q.success is False and "任务不存在" in q.error_message


async def test_delegation_status_query_mid_flight() -> None:
    """进行中的委派任务可查：状态 + 快照（指令在快照里）。"""
    registry, ledger, bus, a, b = _setup()
    res = await registry.invoke(_inv("framework_delegate", {"agent": "agent_b", "instruction": "去挖矿"}))
    task_id = res.structured_content["task_id"]
    b.received.clear()  # 仅查询，不推进

    q = await registry.invoke(_inv("framework_task_status", {"task_id": task_id}))
    assert q.success is True
    data = q.structured_content
    assert data["status"] == "accepted"  # 尚未开始执行
    assert data["initiator"] == "agent_a" and data["executor"] == "agent_b"
    assert data["snapshot"]["instruction"] == "去挖矿"


# ---------------------------------------------------------------------------
# 受理失败与任务失败分开
# ---------------------------------------------------------------------------


async def test_acceptance_failures_do_not_register() -> None:
    """三类受理失败（不存在 / 拒收 / 自派）→ 失败结果且不登记任务。"""
    registry, ledger, bus, a, b = _setup(refuse_target=True)

    # 目标不存在
    r1 = await registry.invoke(_inv("framework_delegate", {"agent": "ghost", "instruction": "干活"}))
    assert r1.success is False and "不在名册" in r1.error_message

    # 目标拒收（明确拒收原因）
    r2 = await registry.invoke(_inv("framework_delegate", {"agent": "agent_b", "instruction": "干活"}))
    assert r2.success is False and "拒收" in r2.error_message and "忙不过来" in r2.error_message

    # 自派（调用方 agent_a 委派 agent_a）
    r3 = await registry.invoke(_inv("framework_delegate", {"agent": "agent_a", "instruction": "自己干"}))
    assert r3.success is False and "自派" in r3.error_message

    assert len(ledger) == 0, "受理失败一律不登记"


async def test_default_refusal_when_no_receive_entry() -> None:
    """未实现接收入口的 stub（默认拒收路径）→ 受理失败。"""

    class _NoEntryAgent(_StubAgent):
        # 删掉 receive_delegation → 走 BaseAgent 默认拒收
        receive_delegation = BaseAgent.receive_delegation  # type: ignore[assignment]

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus)
    manager = AgentManager()
    a = _StubAgent("agent_a")
    c = _NoEntryAgent("agent_c")
    manager.register(a)
    manager.register(c)
    registry.register_provider(build_agent_control_provider(manager, ledger))

    res = await registry.invoke(_inv("framework_delegate", {"agent": "agent_c", "instruction": "试试"}))
    assert res.success is False and "拒收" in res.error_message
    assert len(ledger) == 0, "默认拒收不登记"


async def test_task_failure_distinct_from_acceptance_failure() -> None:
    """执行中失败 → 记录表 failed 终态（与受理失败形态不同：任务已登记过）。"""
    registry, ledger, bus, a, b = _setup()
    res = await registry.invoke(_inv("framework_delegate", {"agent": "agent_b", "instruction": "会失败的工作"}))
    task_id = res.structured_content["task_id"]
    assert res.success is True

    await b.run_and_finish(ledger, failed=True)
    await _drain()

    q = await registry.invoke(_inv("framework_task_status", {"task_id": task_id}))
    assert q.success is False and "任务不存在" in q.error_message  # failed 终态已移除
    # 终态事件曾以 failed 发出（对账：直接查 ledger 已移除，事件由 bus 转发）


async def test_unknown_task_id_query_fails() -> None:
    """未知任务号查询 → 失败结果（任务不存在）。"""
    registry, ledger, bus, a, b = _setup()
    q = await registry.invoke(_inv("framework_task_status", {"task_id": "nope-123"}))
    assert q.success is False and "任务不存在" in q.error_message


# ---------------------------------------------------------------------------
# minecraft 接收委派（真实 Agent：入队带任务号 + 批次状态写回）
# ---------------------------------------------------------------------------


async def test_minecraft_receives_delegation_and_reports_terminal() -> None:
    """framework_delegate → minecraft 入队（带任务号）→ 批次 running → delivery → succeeded。"""
    from unittest.mock import AsyncMock, MagicMock

    from src.agents.minecraft.agent import MinecraftAgent
    from src.agents.minecraft.config import MinecraftConfig
    from src.modules.mcp.config import McpServerConfig

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus)
    manager = AgentManager()
    tracker = TaskTracker(registry, ledger, poll_interval_ms=20)

    llm = MagicMock()
    llm.chat_messages = AsyncMock(side_effect=[])
    minecraft = MinecraftAgent(
        MinecraftConfig(max_steps=5, mcp=McpServerConfig(enabled=False)),
        llm_manager=llm,
        event_bus=bus,
        tool_registry=registry,
        task_tracker=tracker,
    )
    manager.register(minecraft)
    registry.register_provider(build_agent_control_provider(manager, ledger))

    # 1. 委派受理（目标 minecraft）
    res = await registry.invoke(
        ToolInvocation(
            tool_name="framework_delegate",
            arguments={"agent": "minecraft", "instruction": "建一座木头房子"},
            source="streamer",
        )
    )
    assert res.success is True
    task_id = res.structured_content["task_id"]
    record = ledger.get(task_id)
    assert record is not None and record.initiator == "streamer" and record.executor == "minecraft"

    # 2. 指令已在队列且带任务号
    assert list(minecraft._message_queue) == [(task_id, "建一座木头房子")]

    # 3. 批次 flush 吸收指令（复刻 _run_task 的消息 flush）→ 委派任务 running
    while minecraft._message_queue:
        tid, _content = minecraft._message_queue.popleft()
        if tid:
            minecraft._delegated_batch_ids.append(tid)
    minecraft._mark_delegated_running()
    record = ledger.get(task_id)
    assert record is not None and record.status == "running"

    # 4. 交付 → succeeded + 终态移除
    await minecraft._handle_report("delivery", "房子建好了", scene="0,0,0")
    assert ledger.get(task_id) is None, "delivery 后委派任务终态移除"
