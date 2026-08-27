"""
@tool 装饰器（Wave 3 / §1.5）

进程内轻量声明：将一个 ``async`` 函数声明为工具，自动生成 ToolSpec 并
注册。**双模式**：

1. **pending 模式（推荐 / 生产路径）**——装饰器不带 ``registry=`` 参数时，
   将 ``(spec, wrapped_fn)`` 追加到模块级 pending 表，**不**触发任何注册。
   由组合根（composition root）在装配阶段显式调用
   ``bind_pending_tools(registry)`` 把 pending 注入指定 registry。
   这样做的好处：
   - 装饰器不再硬依赖 ``default_tool_registry()`` 全局单例
   - 组合根完全控制注册时机 / 目标 registry / 是否注入
   - pending 表可被审计（哪些工具待绑定）

2. **explicit 模式（测试 / 本地 scope）**——装饰器带显式 ``registry=`` 参数时，
   保持原立即注册语义，调用方完全掌控（构造独立 ``ToolRegistry()`` 注入）。
   仅用于测试或一次性脚本，**生产路径禁止**——会让注册时机与导入耦合。

注意：
- **不取代 ToolProvider**：有状态的（MCP/游戏 Agent）请直接实现 Provider，
  然后 ``registry.register_provider(provider)``
- 默认 ``provider="builtin"``（§1.5 来源溯源）
- 入口装配示例：
  ````python
  from src.modules.tools.bootstrap import bind_core_tools
  from src.modules.tools.decorator import bind_pending_tools
  from src.modules.tools import ToolRegistry

  registry = ToolRegistry()
  bind_core_tools(registry, config)  # 注入 output/* 的 L2 Provider
  bind_pending_tools(registry)  # 刷入所有 @tool 装饰的 L1
  ````
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from src.modules.logging import get_logger
from src.modules.tools.models import (
    DEFAULT_RESULT_EVENT_PREFIX,
    Kind,
    Provider,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.registry import ToolRegistry

logger = get_logger("ToolDecorator")


# =============================================================================
# Pending 表：模块级（进程内单例）
# =============================================================================
# 每条形如 (ToolSpec, impl_callable)，按装饰顺序追加；bind_pending_tools
# 按顺序刷入目标 registry（first-wins 由 ToolRegistry.register 保证）。

_PENDING_TOOLS: List[Tuple[ToolSpec, Callable[..., Awaitable[ToolExecutionResult]]]] = []


def _take_pending() -> List[Tuple[ToolSpec, Callable[..., Awaitable[ToolExecutionResult]]]]:
    """原子地取出 pending 表内容并清空（供 bind_pending_tools 使用）。

    以"取出+清空"的整体操作避免并发场景下重复绑定；测试间亦通过此路径隔离。
    """
    items = list(_PENDING_TOOLS)
    _PENDING_TOOLS.clear()
    return items


def _pending_count() -> int:
    """返回当前 pending 表中待绑定条目数（仅诊断 / 测试用）。"""
    return len(_PENDING_TOOLS)


def _clear_pending() -> None:
    """清空 pending 表（仅诊断 / 测试用）。"""
    _PENDING_TOOLS.clear()


def bind_pending_tools(registry: ToolRegistry) -> int:
    """把 pending 表中的所有工具刷入 ``registry``，返回新注册数。

    语义：
    - 按追加顺序逐个调用 ``registry.register(spec, impl)``
    - 重复 name → 由 ``ToolRegistry.register`` 自动去重（first-wins），
      本函数只统计新注册数（重复条目不计入）
    - 重复调用安全：上次刷入后 pending 表已清空，第二次调用返回 0

    Args:
        registry: 目标注册器（composition root 构造并持有）

    Returns:
        本次实际新注册的工具数（≥ 0）
    """
    pending = _take_pending()
    if not pending:
        logger.debug("bind_pending_tools: 无 pending 工具，跳过")
        return 0

    new_count = 0
    for spec, impl in pending:
        if registry.register(spec, impl):
            new_count += 1

    logger.info(
        f"bind_pending_tools: 已刷入 {new_count}/{len(pending)} 个工具到 registry（去重不计={len(pending) - new_count}）"
    )
    return new_count


# =============================================================================
# 装饰器
# =============================================================================


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
    """声明一个工具（pending 模式 / 显式 registry 模式二选一）。

    - **pending 模式**（生产推荐）：不传 ``registry=``，装饰器把 ``(spec, impl)``
      追加到 pending 表，由组合根在装配阶段调用
      ``bind_pending_tools(registry)`` 注入指定 registry。
    - **explicit 模式**（测试 / 本地）：传入 ``registry=ToolRegistry(...)``，
      装饰器立即注册到该 registry（不接触全局单例，行为可预测）。

    使用示例：

    .. code-block:: python

        # pending 模式（生产）
        @tool(description="看游戏画面", parameters_schema=LOOK_AT_SCREEN_SCHEMA)
        async def look_at_screen(invocation: ToolInvocation) -> ToolExecutionResult: ...


        # explicit 模式（测试）
        _reg = ToolRegistry()


        @tool(description="speak", registry=_reg)
        async def speak(invocation: ToolInvocation) -> ToolExecutionResult: ...
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

        # 暴露 spec 给需要时查询（两种模式都设置，便于调试 / 审计）
        _wrapped.tool_spec = spec  # type: ignore[attr-defined]

        # 注册策略：explicit 模式（registry= 显式传入）→ 立即；pending 模式 → 排队
        if registry is not None:
            registry.register(spec, _wrapped)
            logger.debug(f"@tool '{tool_name}' 已显式注册到传入 registry（provider={spec.provider}, kind={spec.kind}）")
        else:
            _PENDING_TOOLS.append((spec, _wrapped))
            logger.debug(
                f"@tool '{tool_name}' 已加入 pending 表（provider={spec.provider}, kind={spec.kind}，待 bind_pending_tools 刷入）"
            )

        return _wrapped

    return decorator


__all__ = [
    "tool",
    "bind_pending_tools",
    # 内部辅助（供测试与诊断使用，组合根不需要）
    "_pending_count",
    "_clear_pending",
]
