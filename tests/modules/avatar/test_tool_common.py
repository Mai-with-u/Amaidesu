"""avatar.tool_common：dispatch 分发三路径、unknown_action_fail 载荷"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.modules.avatar.tool_common import dispatch_tool, unknown_action_fail
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, tool_result_ok


def _invoke(tool: str, **arguments):
    return ToolInvocation(tool_name=tool, arguments=arguments, source="test")


async def test_dispatch_tool_hits_handler_and_covers_unknown_and_exception() -> None:
    ok_handler = AsyncMock(return_value=tool_result_ok("vts_set_expression", True))
    logger = MagicMock()
    handlers = {"vts_set_expression": ok_handler}

    result = await dispatch_tool(_invoke("vts_set_expression", emotion="happy"), "vts", handlers, logger)
    assert result.success is True
    ok_handler.assert_awaited_once()

    unknown = await dispatch_tool(_invoke("vts_nope"), "vts", handlers, logger)
    assert unknown.success is False
    assert unknown.error_message == "工具 'vts_nope' 不属于 Provider 'vts'"

    def _boom() -> ToolExecutionResult:
        raise RuntimeError("连接已断")

    failure = await dispatch_tool(_invoke("vts_boom"), "vts", {"vts_boom": _boom}, logger)
    assert failure.success is False and failure.error_message == "RuntimeError: 连接已断"
    logger.exception.assert_called_once()  # 异常路径携带原因上下文，不静默吞


def test_unknown_action_fail_carries_catalog() -> None:
    result = unknown_action_fail("vts_trigger_preset_action", "不存在", ["Wave", "Nod"])
    assert result.success is False
    assert result.error_message == "未知预设动作 '不存在'"
    assert result.structured_content == {"available_actions": ["Wave", "Nod"]}
    assert result.content == str(["Wave", "Nod"])
