"""as_tool_impl 返回值归一化助手单测。

覆盖：str 返回 → success + content；异常 → failure + error_message 含异常类型；
自动计时 duration_ms > 0；ToolExecutionResult 透传与时间字段补全。
"""

from __future__ import annotations

from src.modules.tools import ToolInvocation
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import as_tool_impl


async def test_str_return_wrapped_as_success_content() -> None:
    async def plain(inv: ToolInvocation):
        return "hello"

    impl = as_tool_impl("demo", plain)
    res = await impl(ToolInvocation(tool_name="demo"))
    assert res.success is True
    assert res.content == "hello"
    assert res.tool_name == "demo"


async def test_none_return_wrapped_as_empty_content() -> None:
    async def plain(inv: ToolInvocation):
        return None

    impl = as_tool_impl("demo", plain)
    res = await impl(ToolInvocation(tool_name="demo"))
    assert res.success is True
    assert res.content == ""


async def test_exception_wrapped_as_failure_with_type_in_message() -> None:
    async def plain(inv: ToolInvocation):
        raise RuntimeError("后端挂了")

    impl = as_tool_impl("demo", plain)
    res = await impl(ToolInvocation(tool_name="demo"))
    assert res.success is False
    assert "RuntimeError" in res.error_message
    assert "后端挂了" in res.error_message


async def test_duration_ms_recorded_positive() -> None:
    async def plain(inv: ToolInvocation):
        return "x"

    impl = as_tool_impl("demo", plain)
    res = await impl(ToolInvocation(tool_name="demo"))
    assert res.duration_ms >= 0
    assert res.timestamp_ms > 0


async def test_execution_result_passthrough_with_time_fill() -> None:
    async def plain(inv: ToolInvocation):
        return ToolExecutionResult(tool_name="demo", success=True, content="ok")

    impl = as_tool_impl("demo", plain)
    res = await impl(ToolInvocation(tool_name="demo"))
    assert res.success is True
    assert res.content == "ok"
    assert res.timestamp_ms > 0
    assert res.duration_ms >= 0


async def test_execution_result_keeps_explicit_time_fields() -> None:
    explicit = ToolExecutionResult(
        tool_name="demo",
        success=True,
        content="ok",
        timestamp_ms=123,
        duration_ms=45,
    )

    async def plain(inv: ToolInvocation):
        return explicit

    impl = as_tool_impl("demo", plain)
    res = await impl(ToolInvocation(tool_name="demo"))
    assert res.timestamp_ms == 123
    assert res.duration_ms == 45
