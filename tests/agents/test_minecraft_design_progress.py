"""失败的机器设计能收到原生诊断，并在无进展的重复拒绝后停止。"""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.design_progress import MachineDesignProgress
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolExecutionResult


def _request() -> dict:
    return {"goal": {"ability": "maicraft:design_machine", "parameters": {"blueprint": {"blocks": []}}}}


def _rejected() -> dict:
    return {"success": False, "error": {"code": "invalid_arguments", "message": "invalid axis", "outcome_known": True}}


def test_changed_blueprint_is_progress_but_request_key_is_not() -> None:
    """换去重键不等于修图，修改方案和新的玩家指令都能恢复尝试。"""
    guard = MachineDesignProgress()
    request, observation = _request(), _rejected()
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
    agent = MinecraftAgent(MinecraftConfig(max_steps=10), llm_manager=llm, event_bus=bus, tool_registry=registry)
    result = await agent._execute_tool("maicraft_execute", _request())
    assert result["ok"] is False and result["error"] == _rejected()["error"]
    registry.invoke.reset_mock()
    agent._running = True
    await agent._run_task_batch()
    assert registry.invoke.await_count == 3
    assert llm.generate.await_count == 3
    assert agent._task_suspended is True
