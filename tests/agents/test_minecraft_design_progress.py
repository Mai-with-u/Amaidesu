"""失败的机器设计能收到原生诊断，并在无进展的重复拒绝后停止。"""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.design_progress import MachineDesignProgress
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolExecutionResult


def _request(ability: str = "maicraft:design_machine") -> dict:
    """各机器入口都提交相同的示例蓝图，验证同一拒绝在不同入口均能正确收束。"""
    return {"goal": {"ability": ability, "parameters": {"blueprint": {"blocks": []}}}}


def _rejected() -> dict:
    return {"success": False, "error": {"code": "invalid_arguments", "message": "invalid axis", "outcome_known": True}}


@pytest.mark.parametrize("ability", ["maicraft:design_machine", "maicraft:build_machine", "maicraft:modify_machine"])
def test_changed_blueprint_is_progress_but_request_key_is_not(ability: str) -> None:
    """换去重键不等于修图，修改方案和新的玩家指令都能恢复尝试。"""
    guard = MachineDesignProgress()
    request, observation = _request(ability), _rejected()
    assert guard.observe("maicraft_execute", request, observation) is None
    request["request_key"] = "another"
    assert guard.observe("maicraft_execute", request, observation) is None
    assert guard.observe("maicraft_perceive", {}, observation) is None
    assert guard.observe("maicraft_execute", request, observation)
    changed = deepcopy(request)
    changed["goal"]["parameters"]["blueprint"]["blocks"] = [{"block_id": "minecraft:stone"}]
    assert guard.observe("maicraft_execute", changed, observation) is None
    guard.reset()
    assert guard.observe("maicraft_execute", request, observation) is None


def test_uncertain_or_accepted_requests_are_not_counted_as_rejections() -> None:
    """异步受理和未知结果沿用任务回执流程，不能触发重复设计失败的停止判断。"""
    guard = MachineDesignProgress()
    uncertain = _rejected()
    uncertain["error"]["outcome_known"] = False
    for _ in range(4):
        assert guard.observe("maicraft_execute", _request(), uncertain) is None
        assert guard.observe("maicraft_execute", _request(), {"accepted": True, "task_id": "task"}) is None


@pytest.mark.asyncio
async def test_react_preserves_diagnostics_and_stops_identical_design_rejections() -> None:
    """实际 ReAct 批次在第三次拒绝后挂起，结构化错误保持完整且没有被压成字符串。"""
    registry = MagicMock()
    registry.list_tools.return_value = []
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="maicraft_execute", success=False, structured_content=_rejected(), error_message="wire rejected"
        )
    )
    llm = MagicMock()
    llm.generate = AsyncMock(
        return_value=Response(
            success=True, tool_calls=[ToolCall(id="design", name="maicraft_execute", arguments=_request())]
        )
    )
    bus = MagicMock()
    bus.emit = AsyncMock()
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=llm, event_bus=bus, tool_registry=registry)
    result = await agent._execute_tool("maicraft_execute", _request())
    assert result["ok"] is False and result["error"] == _rejected()["error"]
    registry.invoke.reset_mock()
    agent._running = True
    await agent._run_task_batch()
    assert registry.invoke.await_count == 3
    assert llm.generate.await_count == 3
    assert agent._task_suspended is True


def test_repeated_validation_rejection_survives_site_refresh() -> None:
    """同一坏蓝图换现场快照后仍是同一拒绝；修订机器结构才解除重复判断。"""
    guard = MachineDesignProgress()
    request = _request("maicraft:build_machine")
    observation = {"status": "needs_revision", "validation": {"valid": False, "errors": ["invalid axis"]}}
    for index in range(3):
        request["goal"]["parameters"]["snapshot_id"] = f"site-{index}"
        result = guard.observe("maicraft_plan", request, observation)
        assert bool(result) is (index == 2)


@pytest.mark.asyncio
async def test_suspension_reports_escalation_and_preserves_delegated_work() -> None:
    """重复读资料导致角色暂停时，主播收到可触发决策的上报，继续指令仍恢复原委派。"""
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=MagicMock())
    tracker = MagicMock()
    agent._task_tracker = tracker
    agent._delegated_finished_ids = ["original-build"]
    agent._emit_report = AsyncMock()
    await agent._suspend_with_report("最后一根传动轴尚未放置，已暂停")
    agent._emit_report.assert_awaited_once_with("escalation", "最后一根传动轴尚未放置，已暂停")
    assert agent._task_suspended and agent._delegated_finished_ids == ["original-build"]
    assert tracker.ledger.update.call_args.args == ("original-build", "waiting_for_decision")
    agent._mark_delegated_running()
    assert tracker.ledger.update.call_args.args == ("original-build", "running")
