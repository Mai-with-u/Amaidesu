"""游戏委派保留具体目标原句，不把来源引用误当成空指令的行动授权。"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.modules.tools.models import ToolExecutionResult


@pytest.mark.asyncio
async def test_delegation_keeps_source_without_rewriting_model_history() -> None:
    """即使规划摘要泛化了目标，接收方仍看到原词和禁用范围；原工具请求对象不被原地修改。

    做什么以主播的决定为准：原话只补物品名写法与禁用条件，做法建议须经目标采纳，
    避免"别死磕这台机器"和"以原话为准的放块铁板试试"同时摆在游戏侧面前。
    """
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="framework_delegate", success=True, structured_content={"accepted": True}
        )
    )
    planner = Planner({}, MagicMock(), MagicMock(), RoomState(), tool_registry=registry)
    original = "在平台上搭建精密构建生产线，不许用Mek"
    source = [{"role": "user", "content": original}]
    arguments = {"agent": "minecraft", "instruction": "搭一条工业风产线"}
    result = await planner._invoke_registry_tool("framework_delegate", arguments, source_dialogue=source)
    delivered = registry.invoke.call_args.args[0].arguments["instruction"]
    assert original in delivered and "不构成额外任务" in delivered
    assert delivered.startswith("[本次目标]\n搭一条工业风产线")
    assert "两者冲突时听本次目标" in delivered and "只有本次目标采纳了才做" in delivered
    assert "以对应来源原话为准" not in delivered
    assert arguments["instruction"] == "搭一条工业风产线"
    assert json.loads(result)["accepted"] is True


@pytest.mark.asyncio
async def test_source_does_not_create_an_instruction_or_change_other_tools() -> None:
    """没有目标时仍交给原有校验拒绝；普通查询不附带观众对话。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=ToolExecutionResult(tool_name="test", success=True, structured_content={}))
    planner = Planner({}, MagicMock(), MagicMock(), RoomState(), tool_registry=registry)
    for tool, arguments in [("framework_delegate", {"instruction": ""}), ("clock_time", {})]:
        await planner._invoke_registry_tool(
            tool, arguments, source_dialogue=[{"role": "user", "content": "另一个请求"}]
        )
        assert registry.invoke.call_args.args[0].arguments == arguments
