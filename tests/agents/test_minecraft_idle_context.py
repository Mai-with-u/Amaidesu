"""续做只补交任务状态变化、等待后台任务时提前整理历史，行动轮不再整份重抄状态或停下来等摘要。"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig, MinecraftContextConfig
from src.agents.minecraft.context import context_chars
from src.agents.minecraft.observation_context import project_context
from src.modules.llm.payload import Response
from src.modules.tools.registry import ToolRegistry


def make_agent(max_context_chars: int = 24000) -> tuple[MinecraftAgent, MagicMock]:
    """直接驱动玩家循环，不连接游戏也不调用真实模型。"""
    llm = MagicMock()
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(context=MinecraftContextConfig(max_context_chars=max_context_chars)),
        llm_manager=llm,
        event_bus=bus,
        tool_registry=ToolRegistry(),
    )
    agent._register_tools()
    agent._running = True
    agent._task_finished = False
    agent._task_instructions = ["用蜂房现成机器做一块蜂蜜胶并放进背包"]
    return agent, llm


def history(rounds: int) -> list[dict[str, Any]]:
    """按轮数积累已读资料，用来把工作历史推到提前整理线或完整预算之上。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": "按已有证据推进游戏任务"}]
    for index in range(rounds):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "读取部件",
                    "tool_calls": [
                        {
                            "id": f"old-{index}",
                            "type": "function",
                            "function": {"name": "maicraft_perceive", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": f"old-{index}", "content": f"已读的第{index}份组件资料" * 300},
            ]
        )
    return messages


def test_continue_context_sends_only_changes_after_full_state() -> None:
    """首次续做交付完整状态；之后只补交变化的待办和后台任务，未变的原始指令只列名。"""
    agent, _ = make_agent()
    agent._task_progress = {
        "a": {"task_id": "a", "status": "running"},
        "b": {"task_id": "b", "status": "running"},
    }
    first = agent._continue_task_context()
    assert first["original_instructions"] == agent._task_instructions and "observations" in first

    agent._mc_state.set_todos([{"content": "把铁板放上置物台", "status": "in_progress"}])
    agent._task_progress["b"]["status"] = "succeeded"
    second = agent._continue_task_context()
    assert "original_instructions" not in second and "observations" not in second
    assert second["todo"][0]["content"] == "把铁板放上置物台"
    assert [task["task_id"] for task in second["background_tasks"]] == ["b"]
    assert "original_instructions" in second["unchanged_since_earlier_task_state"]

    # 换任务或整理后基准清空，下一次续做重新完整展示。
    agent._shown_context = {}
    assert "original_instructions" in agent._continue_task_context()


@pytest.mark.asyncio
async def test_waiting_compacts_history_before_the_next_action_round() -> None:
    """历史过了提前整理线但未到预算：让出等待时在后台整理，醒来直接使用整理好的历史。"""
    agent, llm = make_agent()
    llm.generate = AsyncMock(
        return_value=Response(success=True, content="已读组件资料，无新缺口", finish_reason="stop")
    )
    messages = history(6)
    size = context_chars(project_context(messages), [])
    assert 24000 * 0.7 < size <= 24000
    agent._messages = messages

    agent._schedule_idle_compaction(messages, [])
    assert agent._idle_compaction is not None
    # 后台任务的游戏时间足够摘要完成；醒来时只需结清已完成的整理。
    await asyncio.wait_for(asyncio.shield(agent._idle_compaction), 5)
    await agent._settle_idle_compaction([])

    assert any("[历史推理摘要" in str(message.get("content")) for message in messages)
    assert agent._shown_context["original_instructions"] == agent._task_instructions


@pytest.mark.asyncio
async def test_wakeup_abandons_unfinished_idle_compaction_when_under_budget() -> None:
    """新事实到达而历史仍在预算内：放弃仍在生成的摘要，原历史完整保留，立刻处理新事实。"""
    agent, llm = make_agent()
    blocked = asyncio.Event()

    async def slow_summary(*_: Any, **__: Any) -> Response:
        await blocked.wait()
        return Response(success=True, content="不应被采用", finish_reason="stop")

    llm.generate = slow_summary
    messages = history(6)
    original = [dict(message) for message in messages]
    agent._messages = messages
    agent._schedule_idle_compaction(messages, [])
    await asyncio.sleep(0)

    await agent._settle_idle_compaction([])
    assert agent._idle_compaction is None
    assert messages == original


@pytest.mark.asyncio
async def test_wakeup_waits_for_idle_compaction_when_over_budget() -> None:
    """醒来时历史已超预算：本来也必须整理，等后台整理完成而不是另起一次。"""
    agent, llm = make_agent()
    released = asyncio.Event()

    async def summary(*_: Any, **__: Any) -> Response:
        await released.wait()
        return Response(success=True, content="已读组件资料，无新缺口", finish_reason="stop")

    llm.generate = summary
    messages = history(6)
    agent._messages = messages
    agent._schedule_idle_compaction(messages, [])
    await asyncio.sleep(0)
    # 等待期间又到了一批回执，醒来时已超过完整预算。
    messages.extend(history(4)[1:])
    assert context_chars(project_context(messages), []) > 24000

    settle = asyncio.create_task(agent._settle_idle_compaction([]))
    await asyncio.sleep(0)
    assert not settle.done()
    released.set()
    await settle
    assert any("[历史推理摘要" in str(message.get("content")) for message in messages)
