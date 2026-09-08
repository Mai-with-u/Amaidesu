"""
ToolProvider Protocol 与 BaseToolProvider 基类

每个提供方（builtin / game / mcp 等）实现 ``ToolProvider`` 接口。
本模块提供两套互补的能力：

- ``ToolProvider`` Protocol——结构性接口契约，运行时按方法名判定 duck typing；
  仍是所有 Provider 实现必须满足的最小集合
- ``BaseToolProvider`` ABC——所有经 ``ToolRegistry.register_provider`` 装配的
  Provider 的继承基类；带 ``category`` ClassVar 收敛与 ``health_check`` 探活钩子默认实现

## 探活契约
``BaseToolProvider.health_check`` 默认实现返回 ``True``，语义为
"无可检查之物，让流量决定"——熔断后冷却期满即恢复，再失败再熔断。

维护外部连接（WebSocket / HTTP / stdio 子进程等）的 Provider **必须**重写此方法做
真实检查：连接可用才返回 ``True``；不可用返回 ``False``；方法内抛异常视作检查
失败，由调用方按不健康处理。

## 工厂
``make_provider_from_specs``：从一组 ``(spec, impl)`` 元组构造固定 provider
（便于内置工具组合）；其内部 ``_SpecImplProvider`` 继承 ``BaseToolProvider``，
对 ``ToolProvider`` Protocol 仍保持结构一致。

## 契约要点
- Provider 知道**自己的**工具；``ToolRegistry`` 负责聚合多个 Provider
- Provider 应保证：
  - ``invoke`` 对自己声明的工具有效（错误转为 ToolExecutionResult，不抛）
  - 未知工具名 → ToolExecutionResult(success=False, error_message=...)
- Provider 通常是无状态的；如有内部资源（连接/引擎），通过构造器注入
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, ClassVar, Iterable, List, Optional, Protocol, runtime_checkable

from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec


# Provider 实现函数的统一签名
# 接收 ToolInvocation，返回 await ToolExecutionResult
ToolImpl = Callable[[ToolInvocation], Awaitable[ToolExecutionResult]]


@runtime_checkable
class ToolProvider(Protocol):
    """工具提供方协议（结构型接口）。"""

    # 提供者自声明归属分类（avatar / studio / vision / memory / game / mcp /
    # framework 等）。三个概念正交：provider = 提供者名（全局唯一，决定注册名
    # 前缀）、category = 分组（供查询/过滤）、tools.toml 段 = 配置地址。
    # 带默认值 → 实现方可省略；registry 以 getattr 兜底读取。
    category: ClassVar[str] = ""

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

    async def health_check(self) -> bool:
        """探活钩子（**默认实现**即返回 True）。

        - 维护外部连接（WebSocket / HTTP / stdio 子进程等）的 Provider 必须
          重写此方法做真实检查：连接可用才返回 True；不可用返回 False
        - 无状态 Provider 沿用默认实现，语义为"无可检查之物，让流量决定"——
          熔断后冷却期满即恢复，再失败再熔断
        - 调用方对抛异常按不健康处理（不会上抛）
        """
        ...


# =============================================================================
# 抽象基类：所有注册到 ToolRegistry 的 Provider 都应继承
# =============================================================================


class BaseToolProvider(ABC):
    """所有经 ``ToolRegistry.register_provider`` 装配的 Provider 的继承基类。

    提供：
    - ``category`` ClassVar 收敛（实现方可继续用子类声明的具体值覆盖）
    - ``health_check`` 探活钩子默认实现（返回 True，语义见模块注释）
    - 抽象方法：``list_tools()`` / ``invoke()``——子类必须实现

    继承本类的 Provider 同时满足 ``ToolProvider`` Protocol（结构一致）：Protocol
    仍可用于外部 duck-typed 验证。``name`` 由子类按需提供（属性或字段均可），
    本类不强制抽象以避免与 ``@dataclass(slots=True)`` 子类字段注解的 property
    默认值冲突。
    """

    # 提供者自声明归属分类（avatar / studio / vision / memory / game / mcp /
    # framework 等）。三个概念正交：provider = 提供者名、category = 分组、
    # tools.toml 段 = 配置地址。子类可覆写具体值；默认空串（未分类）。
    category: ClassVar[str] = ""

    @abstractmethod
    def list_tools(self) -> Iterable[ToolSpec]:
        """列出本 Provider 暴露的所有 ToolSpec——子类必须实现。"""
        ...

    @abstractmethod
    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """执行工具调用——子类必须实现（永不抛异常）。"""
        ...

    async def health_check(self) -> bool:
        """探活钩子默认实现：返回 True（无可检查之物）。

        维护外部连接（WebSocket / HTTP / stdio 子进程等）的 Provider **必须**
        重写此方法做真实检查（连接可用才返回 True；不可用返回 False）。无状态
        Provider 沿用默认实现——熔断后冷却期满即恢复，再失败再熔断，由流量决定。
        """
        return True


# =============================================================================
# 工厂：make_provider_from_specs
# =============================================================================


@dataclass(slots=True)
class _SpecImplProvider(BaseToolProvider):
    """由 ``make_provider_from_specs`` 返回的固定 Provider。"""

    name: str
    spec_impl_pairs: List[tuple[ToolSpec, ToolImpl]]  # noqa: UP006 ——slots + List 在 3.12 兼容
    fallback_result_factory: Optional[Callable[[ToolInvocation], ToolExecutionResult]] = None
    category: str = ""

    def list_tools(self) -> Iterable[ToolSpec]:
        return [spec for spec, _impl in self.spec_impl_pairs]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        for spec, impl in self.spec_impl_pairs:
            # 注册名可能带 "<provider>_" 前缀（register_provider 统一改写）；
            # 本 Provider 只认声明时的裸名，比对上剥前缀等价形式
            if spec.name == invocation.tool_name or (
                spec.provider and invocation.tool_name == f"{spec.provider}_{spec.name}"
            ):
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
    category: str = "",
) -> ToolProvider:
    """用一组 ``(spec, impl)`` 构造一个固定的 Provider。

    适用场景：内置工具（无状态、轻）；GameAgent/MCP 推荐手写类实现
    ``BaseToolProvider``（多状态/多步骤/有外部连接）。
    """
    return _SpecImplProvider(name=name, spec_impl_pairs=spec_impl_pairs, category=category)


__all__ = ["ToolProvider", "BaseToolProvider", "ToolImpl", "make_provider_from_specs"]


# 让 Pylance 不报 _ 变量未用
_ = Any
