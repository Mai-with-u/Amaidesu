"""复现缺料后反复查询与摘要的循环，验证新回执可读、决策不丢、重复阅读不算推进。"""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig, MinecraftContextConfig
from src.agents.minecraft.context import MinecraftHistoryCompactor
from src.agents.minecraft.observations import json_text
from src.modules.llm.payload import Response
from src.modules.tools.registry import ToolRegistry


def make_agent() -> tuple[MinecraftAgent, MagicMock]:
    """直接驱动玩家循环，所有回执来自测试，既不连接游戏也不调用真实模型。"""
    llm = MagicMock()
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(
        MinecraftConfig(context=MinecraftContextConfig(max_context_chars=24000)),
        llm_manager=llm,
        event_bus=bus,
        tool_registry=ToolRegistry(),
    )
    agent._register_tools()
    agent._running = True
    agent._task_finished = False
    agent._task_instructions = ["处理当前材料缺口，保留平台和目标产物"]
    return agent, llm


def previous_history() -> list[dict[str, Any]]:
    """积累已读资料，为后续大回执制造真实的上下文整理条件。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": "按已有证据推进游戏任务"}]
    for index in range(8):
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
                {"role": "tool", "tool_call_id": f"old-{index}", "content": "已读的组件资料" * 700},
            ]
        )
    return messages


@pytest.mark.asyncio
async def test_latest_large_receipt_reaches_decision_model_in_full() -> None:
    """即使单份新回执超过整理阈值，实际决策也须先读到全文，不能先被摘要替换掉。"""
    agent, llm = make_agent()
    messages = previous_history()
    raw = json_text({"layout": "原生安装证据" * 7000, "decision_id": "末尾的待应答编号"})
    messages.extend(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "fresh",
                        "type": "function",
                        "function": {"name": "maicraft_task", "arguments": '{"action":"get"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "fresh", "content": raw},
        ]
    )
    agent._messages = messages
    decisions: list[list[dict[str, Any]]] = []

    async def generate(history: list[dict[str, Any]], **kwargs: Any) -> Response:
        if history[0]["content"].startswith("你在整理 Minecraft"):
            assert raw not in [message.get("content") for message in history]
            return Response(success=True, content="旧资料已整理", finish_reason="stop")
        decisions.append(deepcopy(history))
        assert any(message.get("tool_call_id") == "fresh" and message["content"] == raw for message in history)
        return Response(success=True, content="已读取完整回执", finish_reason="stop")

    llm.generate = AsyncMock(side_effect=generate)
    await agent._run_task_batch()
    assert len(decisions) == 1 and agent._context_compactor.checkpoints == 1
    assert llm.generate.await_count == 2


@pytest.mark.asyncio
async def test_single_unread_receipt_does_not_trigger_repeated_summaries() -> None:
    """没有可整理的旧轮次时，把完整新回执交给下一次决策，不反复总结同一份材料。"""
    llm = MagicMock()
    llm.generate = AsyncMock()
    compactor = MinecraftHistoryCompactor(llm, MinecraftContextConfig(max_context_chars=24000))
    messages = previous_history()[:3]
    messages[-1]["content"] = "必须先读到的新回执" * 6000
    before = deepcopy(messages)
    assert not await compactor.compact(messages, [], {})
    assert messages == before
    llm.generate.assert_not_called()
