"""复现缺料后反复查询与摘要的循环，验证新回执可读、决策不丢、重复阅读不算推进。"""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig, MinecraftContextConfig
from src.agents.minecraft.context import MinecraftHistoryCompactor
from src.agents.minecraft.observations import json_text
from src.modules.events.payloads.tasks import TaskChangedPayload
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


def decision_snapshot() -> dict[str, Any]:
    """原生结果既有冗长蓝图，也有必须直接用于恢复的编号、选项和实际材料缺口。"""
    return {
        "task_id": "build",
        "state": "waiting_for_decision",
        "decision": {
            "decision_id": "material-decision",
            "question": "处理剩余材料缺口",
            "options": [{"choice": "recover", "description": "先完成备料前置需求"}],
            "context": {
                "ability": "maicraft:build_machine",
                "goal": {"parameters": {"blueprint": {"description": "无需重复保存的蓝图" * 1000}}},
                "failure": {
                    "message": "前置原料未取得",
                    "data": {
                        "failure_code": "material_batch_supply_failed",
                        "last_native_stage": {
                            "batches": [
                                {
                                    "supply": {
                                        "item_ids": ["create:item_vault"],
                                        "required_final_count": 7,
                                        "observed_final_count": 5,
                                        "missing": 2,
                                        "outcome_uncertain": False,
                                    }
                                }
                            ]
                        },
                    },
                },
            },
        },
    }


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
@pytest.mark.parametrize("receipt_form", ["event", "task", "attention"])
async def test_pending_decision_survives_summary_without_model_restatement(receipt_form: str) -> None:
    """摘要不复述编号时仍保留应答事实，注意流、任务查询和 attention 的原文路径均可定位。"""
    agent, llm = make_agent()
    snapshot = decision_snapshot()
    if receipt_form == "event":
        agent.on_task_notification(
            TaskChangedPayload(
                task_id="build",
                status="waiting_for_decision",
                initiator="minecraft",
                snapshot={"event_type": "decision", "task_id": "build", "data": snapshot["decision"]},
                summary="处理剩余材料缺口",
            )
        )
    else:
        tool = "maicraft_task" if receipt_form == "task" else "maicraft_perceive"
        request = {"action": "get", "task_id": "build"} if receipt_form == "task" else {"view": "attention"}
        receipt = snapshot if receipt_form == "task" else {"task": snapshot}
        shown = agent._observations.present(tool, request, receipt)
        agent._remember_result(tool, request, receipt, shown)
    llm.generate = AsyncMock(return_value=Response(success=True, content="部件资料已整理", finish_reason="stop"))
    messages = previous_history()
    assert await agent._context_compactor.compact(messages, [], agent._current_task_context())
    assert "material-decision" in messages[1]["content"] and '"missing":2' in messages[1]["content"]
    assert '"choice":"recover"' in messages[1]["content"]
    assert "无需重复保存的蓝图" not in messages[1]["content"]
    assert "material-decision" not in messages[2]["content"]
    decision = agent._current_task_context()["background_tasks"][0]["decision"]
    missing = next(row for row in decision["failure_evidence"] if row.get("missing") == 2)
    original = agent._read_observation({"ref": decision["result_ref"], "path": missing["path"]})
    assert '"missing":2' in original["text"]
    snapshot["decision"]["decision_id"] = "外部修改"
    assert agent._current_task_context()["background_tasks"][0]["decision"]["decision_id"] == "material-decision"


def test_new_decision_replaces_old_and_only_confirmed_resume_clears_it() -> None:
    """恢复失败不能删除待应答编号；新决策替换旧编号，恢复受理后立即清掉已过期的选项。"""
    agent, _ = make_agent()
    snapshot = decision_snapshot()
    agent._remember_result("maicraft_task", {"action": "get"}, snapshot, {})
    snapshot["decision"]["decision_id"] = "new-decision"
    agent._remember_result("maicraft_perceive", {"view": "attention"}, {"task": snapshot}, {})
    agent._remember_result(
        "maicraft_task",
        {"action": "answer", "task_id": "build"},
        {"ok": False, "error": {"code": "invalid_arguments"}},
        {},
    )
    assert agent._current_task_context()["background_tasks"][0]["decision"]["decision_id"] == "new-decision"
    agent._remember_result(
        "maicraft_task", {"action": "answer", "task_id": "build"}, {"task_id": "build", "state": "running"}, {}
    )
    assert "decision" not in agent._current_task_context()["background_tasks"][0]


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
