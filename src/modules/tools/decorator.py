"""
@tool 装饰器（Wave 3 / §1.5）

进程内轻量声明：将一个 ``async`` 函数声明为工具，自动生成 ToolSpec 并
注册到全局默认注册器。**简易声明 path**：测试、内置轻量工具首选。

注意：
- **不取代 ToolProvider**：有状态的（MCP/游戏 Agent）请直接实现 Provider，
  然后 ``registry.register_provider(provider)``
- 装饰器注入全局默认注册器（``default_tool_registry()``）；需要隔离时可
  显式 ``registry = ToolRegistry()`` + ``registry.register_tool(spec, impl)``
- 默认 provider="builtin"（§1.5 来源溯源）
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Awaitable, Callable, Optional

from src.modules.tools.models import (
    DEFAULT_RESULT_EVENT_PREFIX,
    Kind,
    Provider,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.registry import ToolRegistry, default_tool_registry


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    parameters_schema: Optional[dict] = None,
    kind: Kind = "sync",
    result_event: str = "",
    provider: Provider = "builtin",
    output_schema: Optional[dict] = None,
    registry: Optional[ToolRegistry] = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[ToolExecutionResult]]]:
    """声明一个工具并注册到全局（或指定）注册器。

    使用示例：
        @tool(description="看游戏画面", parameters_schema=LOOK_AT_SCREEN_SCHEMA)
        async def look_at_screen(invocation: ToolInvocation) -> ToolExecutionResult:
            ...

        @tool(name="speak", kind="async", description="主播说话")
        async def speak(invocation: ToolInvocation) -> ToolExecutionResult:
            ...
    """

    def decorator(
        fn: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[ToolExecutionResult]]:
        tool_name = name or fn.__name__
        tool_description = description or (inspect.getdoc(fn) or "").strip() or "(未提供描述)"
        tool_result_event = result_event or (f"{DEFAULT_RESULT_EVENT_PREFIX}{tool_name}" if kind == "async" else "")

        spec = ToolSpec(
            name=tool_name,
            description=tool_description,
            parameters_schema=parameters_schema,
            kind=kind,
            result_event=tool_result_event,
            provider=provider,
            output_schema=output_schema,
        )

        # 内部把原函数的返回值/异常包装成 ToolExecutionResult
        @wraps(fn)
        async def _wrapped(invocation: ToolInvocation) -> ToolExecutionResult:
            import time as _time

            started_ms = int(_time.time() * 1000)
            try:
                return_value = await fn(invocation)
            except Exception as exc:  # noqa: BLE001 - 边界处兜底
                return ToolExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error_message=f"{type(exc).__name__}: {exc}",
                    timestamp_ms=int(_time.time() * 1000),
                    duration_ms=int(_time.time() * 1000) - started_ms,
                )
            # 返回值已是 ToolExecutionResult → 透传
            if isinstance(return_value, ToolExecutionResult):
                if return_value.timestamp_ms == 0:
                    return_value.timestamp_ms = int(_time.time() * 1000)
                if return_value.duration_ms == 0:
                    return_value.duration_ms = int(_time.time() * 1000) - started_ms
                return return_value
            # 其他返回值 → 包成成功 result
            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                content=str(return_value) if return_value is not None else "",
                timestamp_ms=int(_time.time() * 1000),
                duration_ms=int(_time.time() * 1000) - started_ms,
            )

        # 选择注册器
        target = registry if registry is not None else default_tool_registry()
        target.register_tool(spec, _wrapped)

        # 暴露 spec 给需要时查询
        _wrapped.tool_spec = spec  # type: ignore[attr-defined]
        return _wrapped

    return decorator


__all__ = ["tool"]
