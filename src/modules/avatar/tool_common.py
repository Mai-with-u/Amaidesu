"""形象平台 Provider 的公共件：invoke 分发骨架与"未知预设动作"失败结果。

各平台适配器都不向 LLM 暴露工具（形象动作统一走 ``avatar_act``），但仍是工具注册表里的
Provider：``invoke`` 收到任何调用都要按统一形状失败返回；调试台触发预设动作时，名字写错
要把目录随结果返回。本模块只收这两处跨平台逐字同构的部分。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import List

from src.modules.logging import ModuleLogger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, tool_result_fail


async def dispatch_tool(
    invocation: ToolInvocation,
    provider_name: str,
    handlers: Mapping[str, Callable[[], Awaitable[ToolExecutionResult]]],
    logger: ModuleLogger,
) -> ToolExecutionResult:
    """invoke 分发骨架：按全名查 handlers 并执行，统一未知名与异常兜底。

    handlers 的键是工具全名（``<provider>_<name>``），值是零参异步闭包——
    闭包捕获本次 invocation 的参数解析结果，每次 invoke 现构造，不做实例
    缓存（缓存会串参数）。未知名与执行异常的失败结果形状各 Provider 一致。
    """
    handler = handlers.get(invocation.tool_name)
    if handler is None:
        return tool_result_fail(
            invocation.tool_name,
            f"工具 '{invocation.tool_name}' 不属于 Provider '{provider_name}'",
        )
    try:
        return await handler()
    except Exception as exc:  # noqa: BLE001 — Provider 边界兜底
        logger.exception(f"{provider_name} 工具 {invocation.tool_name} 调用异常: {exc}")
        return tool_result_fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")


def unknown_action_fail(tool_name: str, action: str, catalog_names: List[str]) -> ToolExecutionResult:
    """未知预设动作的失败结果；目录随结果返回（失败即发现，调用方可自纠）。"""
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error_message=f"未知预设动作 '{action}'",
        structured_content={"available_actions": catalog_names},
        content=str(catalog_names),
    )


__all__ = ["dispatch_tool", "unknown_action_fail"]
