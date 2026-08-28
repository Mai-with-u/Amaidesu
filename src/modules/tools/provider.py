"""
ToolProvider Protocol（Wave 3 / §1.5）

每个提供方（builtin / game）实现 ``ToolProvider`` 接口；``"mcp"`` 是预留枚举值，v2 决策架构移除 MCP 桥接后暂无实现。
- ``list_tools() -> Iterable[ToolSpec]`` 声明自己拥有的工具
- ``invoke(invocation) -> ToolExecutionResult`` 执行（同步语义：sync 工具等到结果；async 工具仅返回成功受理）

辅助工厂 ``make_provider_from_specs``：从一组 ``(spec, impl)`` 元组构造
固定 provider（便于内置工具组合）。

## 契约要点
- Provider 知道**自己的**工具；``ToolRegistry`` 负责聚合多个 Provider
- Provider 应保证：
  - ``invoke`` 对自己声明的工具有效（错误转为 ToolExecutionResult，不抛）
  - 未知工具名 → ToolExecutionResult(success=False, error_message=...)
- Provider 通常是无状态的；如有内部资源（连接/引擎），通过构造器注入
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Protocol, runtime_checkable

from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec


# Provider 实现函数的统一签名
# 接收 ToolInvocation，返回 await ToolExecutionResult
ToolImpl = Callable[[ToolInvocation], Awaitable[ToolExecutionResult]]


@runtime_checkable
class ToolProvider(Protocol):
    """工具提供方协议"""

    @property
    def name(self) -> str:
        """Provider 的标识（用于日志 / 去重）"""
        ...

    def list_tools(self) -> Iterable[ToolSpec]:
        """列出本 Provider 暴露的所有 ToolSpec（每次可动态变化）"""
        ...

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """执行工具调用。

        契约：
        - 工具归属此 Provider：执行并返回结果（成功或失败）
        - 工具不是自己的：返回失败 ToolExecutionResult，不抛异常
        - 永远不抛异常
        """
        ...


# =============================================================================
# 工厂：make_provider_from_specs
# =============================================================================


@dataclass(slots=True)
class _SpecImplProvider:
    """由 ``make_provider_from_specs`` 返回的固定 Provider。"""

    name: str
    spec_impl_pairs: List[tuple[ToolSpec, ToolImpl]]  # noqa: UP006 ——slots + List 在 3.12 兼容
    fallback_result_factory: Optional[Callable[[ToolInvocation], ToolExecutionResult]] = None

    def list_tools(self) -> Iterable[ToolSpec]:
        return [spec for spec, _impl in self.spec_impl_pairs]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        for spec, impl in self.spec_impl_pairs:
            if spec.name == invocation.tool_name:
                return await impl(invocation)
        # 未知：兜底失败（不抛）
        if self.fallback_result_factory is not None:
            return self.fallback_result_factory(invocation)
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=False,
            error_message=f"工具 '{invocation.tool_name}' 不属于 Provider '{self.name}'",
        )


def make_provider_from_specs(
    name: str,
    spec_impl_pairs: List[tuple[ToolSpec, ToolImpl]],  # noqa: UP006 ——见上
) -> ToolProvider:
    """用一组 ``(spec, impl)`` 构造一个固定的 Provider。

    适用场景：内置工具（无状态、轻）；GameAgent/MCP 推荐手写类实现
    ``ToolProvider`` 协议（多状态/多步骤）。
    """
    return _SpecImplProvider(name=name, spec_impl_pairs=spec_impl_pairs)


__all__ = ["ToolProvider", "ToolImpl", "make_provider_from_specs"]


# 让 Pylance 不报 _ 变量未用
_ = Any
