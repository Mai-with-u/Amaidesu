"""验证玩家让出后零推理等待、需要行动时拒绝等待，以及调用回执完整性。"""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.llm.payload import Response, ToolCall
from src.modules.llm.errors import LLMInterruptedError
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger, TaskTracker


def make_agent() -> tuple[MinecraftAgent, Any, TaskTracker]:
    """直接驱动批次和跟踪单步，不连接游戏、不启动后台定时器。"""
    registry = ToolRegistry()
    ledger = TaskLedger()
    tracker = TaskTracker(registry, ledger)
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(
            success=True, tool_calls=[ToolCall(id="wait", name="minecraft_wait", arguments={"reason": "等待施工"})]
        )
    )
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(), llm_manager=llm, tool_registry=registry, task_tracker=tracker, event_bus=bus
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


@pytest.mark.asyncio
async def test_wait_preserves_task_and_only_completion_needs_another_decision() -> None:
    """普通核查和受理转运行不触发推理，完成事件才继续原任务。"""
    agent, llm, tracker = make_agent()
    agent.receive_prompt(content="按已批准设计施工", source="test")
    await agent._run_task_batch()
    assert llm.generate.await_count == 1
    assert agent._wait_requested and not agent._task_finished
    assert not agent._task_reported and agent._task_steps == 1
    sent_history = deepcopy(agent._messages)
    for _ in range(4):
        tracker.ledger.update("work", "running", snapshot={"progress": 20})
        await tracker.step()
    assert llm.generate.await_count == 1
    assert not agent._message_queue
    tracker.ledger.update("work", "succeeded")
    agent.on_task_notification(
        TaskChangedPayload(
            task_id="work", status="succeeded", initiator="minecraft", executor="maicraft", snapshot={"installed": True}
        )
    )
    llm.generate.return_value = Response(success=True, content="已核验施工完成")
    await agent._run_task_batch()
    assert llm.generate.await_count == 2 and agent._task_finished
    # 等待完成恢复时原有调用与回执仍为完全相同的前缀，新任务才清空旧任务的上下文。
    assert agent._messages[:len(sent_history)] == sent_history
    agent.receive_prompt(content="报告当前位置", source="test")
    await agent._run_task_batch()
    assert "按已批准设计施工" not in str(agent._messages)
    assert agent._context_compactor.checkpoints == 0


def test_wait_requires_running_dependency_and_no_unhandled_decision() -> None:
    """尚未开工、待应答、或已经有新消息时，让模型先处理可行动的信息。"""
    agent, _, tracker = make_agent()
    tracker.ledger.update("work", "waiting_for_decision")
    assert agent._request_wait()["ok"] is False
    tracker.ledger.update("work", "succeeded")
    assert agent._request_wait()["ok"] is False
    tracker.ledger.register(
        task_id="next",
        provider="maicraft",
        tool="execute",
        initiator="minecraft",
        executor="maicraft",
        source="provider",
    )
    agent._message_queue.append(("", "现场发生变化"))
    assert agent._request_wait()["waiting"] is False
    assert not agent._wait_requested


def test_accepted_recovery_immediately_allows_host_wait() -> None:
    """恢复回执已是 running 时无需多查一次任务，旧决策不能继续阻止宿主等待。"""
    agent, llm, tracker = make_agent()
    tracker.ledger.update("work", "waiting_for_decision")
    agent._task_progress["work"] = {"task_id": "work", "status": "waiting_for_decision"}
    agent._remember_result(
        "maicraft_task", {"action": "answer", "task_id": "work"},
        {"task_id": "work", "state": "running"}, {},
    )
    assert tracker.ledger.get("work").status == "running"
    assert agent._current_task_context()["background_tasks"][0]["status"] == "running"
    assert agent._request_wait()["waiting"] is True
    llm.generate.assert_not_called()


def test_new_decision_in_same_status_is_delivered_once() -> None:
    """恢复期间再次缺料会产生新决策，即使台账漏过 running 窗口也不能丢失这次通知。"""
    agent, llm, tracker = make_agent()
    agent._task_finished = False
    tracker.ledger.update("work", "waiting_for_decision")
    for decision_id in ("capacity", "capacity", "restore", "restore"):
        agent._absorb_task_event(
            {"type": "decision", "task_id": "work", "data": {"decision_id": decision_id}, "message": "处理当前缺口"}
        )
    assert len(agent._message_queue) == 2
    assert "capacity" in agent._message_queue[0][1] and "restore" in agent._message_queue[1][1]
    llm.generate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [True, False])
async def test_failed_generation_preserves_plan_and_suspends_automatic_wakeups(interrupted: bool) -> None:
    """生成超时或返回失败时没有新工具执行，保留当前计划，并等待真实新指令而非身体事件自动重试。"""
    agent, llm, _ = make_agent()
    agent._task_finished = False
    agent._task_instructions = ["在平台搭建机器"]
    agent._plan_facts.observe("maicraft_plan", {"goal": {"ability": "maicraft:build_machine"}},
                              {"plan_id": "existing-plan", "ready_to_execute": True}, "plan-ref")
    if interrupted:
        llm.generate.side_effect = LLMInterruptedError("流式输出触达硬超时")
    else:
        llm.generate.return_value = Response(success=False, error="provider unavailable")
    agent.receive_prompt(content="沿已验证方案继续", source="test")
    await agent._run_task_batch()
    assert agent._task_suspended and not agent._task_finished
    assert agent._plan_facts.pending()[0]["plan_id"] == "existing-plan"
    assert llm.generate.await_count == 1 and agent._task_steps == 1


@pytest.mark.asyncio
async def test_wait_mixed_with_actions_returns_all_results_without_yielding() -> None:
    """模型把等待和记笔记并排提交时，拒绝等待并保留每项回执供下一轮纠正。"""
    agent, llm, _ = make_agent()
    captured: list[list[dict[str, Any]]] = []

    async def generate(messages: list[dict[str, Any]], **kwargs: Any) -> Response:
        captured.append(deepcopy(messages))
        if len(captured) == 1:
            return Response(
                success=True,
                tool_calls=[
                    ToolCall(id="wait", name="minecraft_wait", arguments={"reason": "等待"}),
                    ToolCall(
                        id="note", name="minecraft_notebook", arguments={"action": "write", "content": "施工已受理"}
                    ),
                ],
            )
        return Response(
            success=True, tool_calls=[ToolCall(id="later", name="minecraft_wait", arguments={"reason": "等待施工终态"})]
        )

    llm.generate = generate
    agent.receive_prompt(content="建好", source="test")
    await agent._run_task_batch()
    receipts = {m["tool_call_id"]: m["content"] for m in captured[1] if m["role"] == "tool"}
    assert "单独调用" in receipts["wait"] and "施工已受理" in receipts["note"]
    assert agent._mc_state.notebook == "施工已受理" and len(captured) == 2
    assert "minecraft_wait" not in {s.full_name for s in agent._tool_registry.list_tools(for_agent="streamer")}
