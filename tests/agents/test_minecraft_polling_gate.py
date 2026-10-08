"""验证 LLM 忙等治理闸：失败唤醒退避、连续失败升级阈值、无进展决策并入等待。

时间相关行为全部经注入时钟确定性推进，不 sleep。
"""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.polling_gate import FailureBackoffGate, NoProgressGate
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker


class FakeClock:
    """毫秒假时钟：测试里手动推进，闸的时间窗口随之确定性地变化。"""

    def __init__(self) -> None:
        self.now_ms = 1_000_000

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, ms: int) -> None:
        self.now_ms += ms


def make_agent(clock: FakeClock) -> tuple[MinecraftAgent, MagicMock, TaskTracker]:
    """不连游戏、不起后台定时器的 agent：跟踪台账里挂一个进行中的后台任务。"""
    registry = ToolRegistry()
    ledger = TaskLedger()
    tracker = TaskTracker(registry, ledger)
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="好"))
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(),
        llm_manager=llm,
        tool_registry=registry,
        task_tracker=tracker,
        event_bus=bus,
        clock=clock,
    )
    agent._running = True
    agent._register_tools()
    ledger.register(
        task_id="work",
        provider="maicraft",
        tool="execute",
        initiator="minecraft",
        executor="maicraft",
        source="provider",
    )
    return agent, llm, tracker


def failed_payload(status: str = "failed", summary: str = "", task_id: str = "work") -> TaskChangedPayload:
    return TaskChangedPayload(
        task_id=task_id,
        status=status,
        initiator="minecraft",
        executor="maicraft",
        snapshot={"task_id": task_id},
        summary=summary,
    )


def cancel_pending_timers(agent: MinecraftAgent) -> None:
    """撤掉尚未到点的退避定时器，避免测试结束时留下挂起的 asyncio 任务。"""
    for task_id in list(agent._failure_wake_timers):
        agent._cancel_failure_wake(task_id)


def test_backoff_progression_recovery_and_post_escalation_throttle() -> None:
    """连续失败逐次退避，达到阈值立即升级，升级后按封顶间隔节流，恢复即清账。"""
    gate = FailureBackoffGate(delays_ms=(30_000, 60_000, 120_000), escalate_after=3)
    assert gate.on_failure("t", "缺料") == (30_000, 1)
    assert gate.on_failure("t", "仍缺料") == (60_000, 2)
    assert gate.on_failure("t", "第三次失败") == (0, 3)  # 阈值：立即升级
    assert gate.on_failure("t", "第四次失败") == (120_000, 4)  # 升级后按封顶间隔节流
    assert gate.facts("t") == ("缺料", "仍缺料", "第三次失败", "第四次失败")
    gate.recover("t")
    assert gate.failure_count("t") == 0
    assert gate.on_failure("t", "重来") == (30_000, 1)


def test_no_progress_gate_window_semantics() -> None:
    """窗口内视为可合并，出窗或复位后重新从提醒开始。"""
    gate = NoProgressGate(window_ms=60_000)
    assert not gate.in_window(1_000)
    gate.mark(1_000)
    assert gate.in_window(61_000)
    assert not gate.in_window(61_001)
    gate.reset()
    assert not gate.in_window(2_000)


@pytest.mark.asyncio
async def test_failed_wakeup_is_deferred_with_failure_facts() -> None:
    """第 1 次失败不立即唤醒 LLM：退避到期注入的消息自带失败事实与连续次数。"""
    clock = FakeClock()
    agent, llm, _ = make_agent(clock)
    agent.on_task_notification(failed_payload(summary="缺料"))
    assert not agent._message_queue  # 没有立即触发决策
    llm.generate.assert_not_called()
    clock.advance(30_000)
    agent._flush_deferred_failure("work")
    assert len(agent._message_queue) == 1
    content = agent._message_queue[0][1]
    assert "缺料" in content and "第 1 次" in content and "退避 30 秒" in content
    cancel_pending_timers(agent)


@pytest.mark.asyncio
async def test_third_consecutive_failure_escalates_immediately() -> None:
    """连续第 3 次失败立即升级唤醒，要求换实质方案或上报；之后继续失败退回封顶节流。"""
    clock = FakeClock()
    agent, llm, _ = make_agent(clock)
    # 同一 payload（状态+摘要+快照全同）会被既有指纹去重拦下，连续失败事件本身携带不同事实
    for summary in ("缺料", "仍缺料"):
        agent.on_task_notification(failed_payload(summary=summary))
        clock.advance(60_000)
        agent._flush_deferred_failure("work")
        agent._message_queue.clear()
    agent.on_task_notification(failed_payload(summary="还是缺料"))
    assert len(agent._message_queue) == 1  # 不等退避，直接升级
    content = agent._message_queue[0][1]
    assert "连续失败 3 次" in content and "换实质不同的方案" in content
    agent._message_queue.clear()
    agent.on_task_notification(failed_payload(summary="第五次"))
    assert not agent._message_queue  # 升级后的继续失败按封顶间隔节流
    clock.advance(120_000)
    agent._flush_deferred_failure("work")
    assert len(agent._message_queue) == 1 and "第五次" in agent._message_queue[0][1]
    cancel_pending_timers(agent)


@pytest.mark.asyncio
async def test_recovery_cancels_pending_failure_wakeup_and_resets_count() -> None:
    """退避期内任务恢复/成功：撤掉定时唤醒，失败账清零，重新失败从头计数。"""
    clock = FakeClock()
    agent, llm, tracker = make_agent(clock)
    agent.on_task_notification(failed_payload(summary="缺料"))
    tracker.ledger.update("work", "succeeded", snapshot={"task_id": "work"})
    agent.on_task_notification(
        TaskChangedPayload(
            task_id="work", status="succeeded", initiator="minecraft", executor="maicraft", snapshot={"task_id": "work"}
        )
    )
    clock.advance(60_000)
    agent._flush_deferred_failure("work")
    # 成功终态本身走正常唤醒路径，但退避攒下的失败唤醒必须已被丢弃
    assert all("缺料" not in str(content) for _, content in agent._message_queue)
    agent._message_queue.clear()
    agent._flush_deferred_failure("work")
    assert not agent._message_queue
    assert agent._failure_gate.failure_count("work") == 0
    agent.on_task_notification(failed_payload(summary="新任务又失败"))
    assert not agent._message_queue  # 从第 1 次退避开始，而不是立即升级
    cancel_pending_timers(agent)


@pytest.mark.asyncio
async def test_new_instruction_resets_gates() -> None:
    """换新方向后旧任务的失败账与退避定时一并作废。"""
    clock = FakeClock()
    agent, llm, _ = make_agent(clock)
    agent.on_task_notification(failed_payload(summary="缺料"))
    agent.receive_prompt(content="换个目标：去钓鱼", source="test")
    llm.generate = AsyncMock(
        return_value=Response(success=True, tool_calls=[ToolCall(id="w", name="minecraft_wait", arguments={})])
    )
    await agent._run_task_batch()
    assert agent._failure_gate.failure_count("work") == 0
    clock.advance(60_000)
    agent._flush_deferred_failure("work")
    assert not agent._message_queue


@pytest.mark.asyncio
async def test_repeated_no_progress_decision_merges_into_wait() -> None:
    """窗口内的第二次无进展决策被框架闸并入等待：不再发起第 4 轮推理。"""
    clock = FakeClock()
    agent, llm, _ = make_agent(clock)
    read_call = ToolCall(id="r", name="minecraft_notebook", arguments={"action": "read"})
    llm.generate = AsyncMock(return_value=Response(success=True, tool_calls=[read_call]))
    agent.receive_prompt(content="施工期间盯着点", source="test")
    await agent._run_task_batch()
    assert llm.generate.await_count == 3  # 第 1 轮首次读取；第 2 轮提醒；第 3 轮再次无进展
    assert agent._wait_requested and not agent._task_suspended  # 并入等待，让出给真实事件
    assert llm.generate.await_count == 3  # 没有第 4 轮推理
    # 真实事件到达后恢复正常唤醒路径
    agent.on_task_notification(failed_payload(summary="施工失败"))
    clock.advance(30_000)
    agent._flush_deferred_failure("work")
    assert agent._message_queue
    cancel_pending_timers(agent)


@pytest.mark.asyncio
async def test_no_progress_gate_resets_after_real_progress() -> None:
    """夹了真实行动后闸窗口清零：第二次无进展重新走提醒路径而不是直接并入等待。"""
    clock = FakeClock()
    agent, _, _ = make_agent(clock)
    read_call = ToolCall(id="r", name="minecraft_notebook", arguments={"action": "read"})
    write_call = ToolCall(id="w", name="minecraft_notebook", arguments={"action": "write", "content": "记录"})
    wait_call = ToolCall(id="x", name="minecraft_wait", arguments={})
    # 写笔记后重读返回新证据（不算重复），闸窗口随真实行动重置；再读一次才重新计无进展
    calls = [read_call, read_call, write_call, read_call, read_call, wait_call]
    made: list[int] = []

    async def generate(messages: list[dict[str, Any]], **kwargs: Any) -> Response:
        call = calls[len(made)]
        made.append(1)
        return Response(success=True, tool_calls=[deepcopy(call)])

    agent._llm.generate = generate
    agent.receive_prompt(content="盯着点", source="test")
    await agent._run_task_batch()
    # 最后一次重复读取在重置后的新窗口内（第 3 轮真实行动清了闸），得到提醒而不是直接并入等待
    assert len(made) == 6
    reminders = [m for m in agent._messages if str(m.get("content") or "").startswith("[任务尚需行动]")]
    assert len(reminders) >= 2
    assert agent._wait_requested and not agent._task_suspended
