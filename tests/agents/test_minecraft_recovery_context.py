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
from src.modules.llm.payload import Response, ToolCall
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


@pytest.mark.asyncio
async def test_repeated_task_queries_remind_once_then_suspend_without_losing_decision() -> None:
    """同一缺料回执反复查询时，先给出可应答的决策事实；仍重复就挂起，避免继续消耗推理。"""
    agent, llm = make_agent()
    receipt = decision_snapshot()
    agent._execute_tool = AsyncMock(return_value=receipt)
    decisions = 0

    async def generate(history: list[dict[str, Any]], **kwargs: Any) -> Response:
        nonlocal decisions
        # 大回执引发的旧历史整理单独响应，不能把摘要请求误当成下一次游戏查询。
        if history[0]["content"].startswith("你在整理 Minecraft"):
            return Response(success=True, content="旧部件资料已整理", finish_reason="stop")
        decisions += 1
        assert decisions <= 4, "重复查询没有收束"
        return Response(
            success=True,
            tool_calls=[
                ToolCall(
                    id=f"get-{decisions}",
                    name="maicraft_task",
                    arguments={"action": "get", "task_id": "build"},
                )
            ],
        )

    llm.generate = AsyncMock(side_effect=generate)
    await agent._run_task_batch()
    assert agent._task_suspended and not agent._task_finished
    assert decisions == 3 and agent._execute_tool.await_count == 3
    reminders = [message for message in agent._messages if "[任务尚需行动]" in (message.get("content") or "")]
    assert len(reminders) == 1 and "material-decision" in reminders[0]["content"]


@pytest.mark.asyncio
async def test_alternating_old_reads_are_not_new_progress() -> None:
    """在两份已读原文之间来回切换也不会推进任务，不能靠换引用绕开空转判断。"""
    agent, llm = make_agent()
    refs = [
        agent._observations.present("knowledge", {"part": part}, {"content": part})["_observation"]["ref"]
        for part in ("传动轴", "机械手")
    ]
    llm.generate = AsyncMock(
        side_effect=[
            Response(
                success=True,
                tool_calls=[
                    ToolCall(
                        id=f"read-{index}",
                        name="minecraft_observation",
                        arguments={"ref": refs[index % 2]},
                    )
                ],
            )
            for index in range(6)
        ]
        + [Response(success=True, content="不应继续空转")]
    )
    await agent._run_task_batch()
    assert agent._task_suspended and llm.generate.await_count == 4


@pytest.mark.asyncio
async def test_changed_world_evidence_allows_further_decisions() -> None:
    """材料数量真的变化时解除上一轮提醒，后续仍可继续观察和完成任务。"""
    agent, llm = make_agent()
    agent._execute_tool = AsyncMock(side_effect=[{"inventory": {"vault": count}} for count in (5, 5, 6, 6, 7)])
    llm.generate = AsyncMock(
        side_effect=[
            Response(
                success=True,
                tool_calls=[
                    ToolCall(
                        id=f"observe-{index}",
                        name="maicraft_perceive",
                        arguments={"view": "situation"},
                    )
                ],
            )
            for index in range(5)
        ]
        + [Response(success=True, content="库存已核验")]
    )
    await agent._run_task_batch()
    assert not agent._task_suspended and agent._task_finished and llm.generate.await_count == 6


@pytest.mark.asyncio
async def test_repeating_the_same_missing_field_is_not_progress() -> None:
    """收到明确路径错误后仍重复读取同一字段时停止空转，修正路径的机会仍保留。"""
    agent, llm = make_agent()
    ref = agent._observations.present("knowledge", {}, {"content": "完整资料"})["_observation"]["ref"]
    llm.generate = AsyncMock(
        side_effect=[
            Response(
                success=True,
                tool_calls=[
                    ToolCall(
                        id=f"bad-{index}",
                        name="minecraft_observation",
                        arguments={"ref": ref, "path": "/data/content"},
                    )
                ],
            )
            for index in range(4)
        ]
        + [Response(success=True, content="不应继续空转")]
    )
    await agent._run_task_batch()
    assert agent._task_suspended and llm.generate.await_count == 3


def test_new_history_pages_and_changed_field_remain_readable() -> None:
    """新页和新的诊断字段提供新信息；只修改分页上限却读到同一全文仍属于重复。"""
    agent, _ = make_agent()
    history = agent._observations
    ref = history.present("knowledge", {}, {"content": "1234567890", "missing": 2})["_observation"]["ref"]
    first = history.read({"ref": ref, "path": "/content", "limit": 5})
    second = history.read({"ref": ref, "path": "/content", "offset": 5, "limit": 5})
    field = history.read({"ref": ref, "path": "/missing"})
    assert not any(row["same_request_and_result"] for row in (first, second, field))
    history.read({"ref": ref})
    duplicate = history.read({"ref": ref, "limit": 1000})
    assert duplicate["same_request_and_result"] and duplicate["complete"]
