"""as_tool_impl 返回值归一化：str/None 包成功结果、异常包失败结果（带异常类型）、自动计时、结果对象透传"""

from __future__ import annotations

import pytest

from src.modules.tools import ToolInvocation
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import as_tool_impl


async def _run(returned):
    async def plain(inv: ToolInvocation):
        if isinstance(returned, Exception):
            raise returned
        return returned

    return await as_tool_impl("demo", plain)(ToolInvocation(tool_name="demo"))


@pytest.mark.parametrize(("returned", "content"), [("hello", "hello"), (None, "")])
async def test_plain_return_is_wrapped_as_success_with_timing(returned, content) -> None:
    res = await _run(returned)
    assert (res.success, res.content, res.tool_name) == (True, content, "demo")
    assert res.duration_ms >= 0 and res.timestamp_ms > 0


async def test_exception_is_wrapped_as_failure_with_type_in_message() -> None:
    res = await _run(RuntimeError("后端挂了"))
    assert res.success is False and "RuntimeError" in res.error_message and "后端挂了" in res.error_message


async def test_execution_result_passes_through_and_fills_or_keeps_time_fields() -> None:
    filled = await _run(ToolExecutionResult(tool_name="demo", success=True, content="ok"))
    assert (filled.content, filled.timestamp_ms > 0, filled.duration_ms >= 0) == ("ok", True, True)

    explicit = await _run(
        ToolExecutionResult(tool_name="demo", success=True, content="ok", timestamp_ms=123, duration_ms=45)
    )
    assert (explicit.timestamp_ms, explicit.duration_ms) == (123, 45)
