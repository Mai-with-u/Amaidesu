"""
ToolProvider Protocol 与 BaseToolProvider 基类

每个提供方（builtin / game / mcp 等）实现 ``ToolProvider`` 接口。
本模块提供两套互补的能力：

- ``ToolProvider`` Protocol——结构性接口契约，运行时按方法名判定 duck typing；
  仍是所有 Provider 实现必须满足的最小集合
- ``BaseToolProvider`` ABC——所有经 ``ToolRegistry.register_provider`` 装配的
  Provider 的继承基类；带 ``category`` ClassVar 收敛、``health_check`` 探活钩子
  默认实现，以及连接动作契约（``connect`` / ``disconnect`` / ``reconnect`` 与
  ``supports_reconnect`` 单点判定）

## 探活契约
``BaseToolProvider.health_check`` 默认实现返回 ``True``，语义为
"无可检查之物，让流量决定"——熔断后冷却期满即恢复，再失败再熔断。

维护外部连接（WebSocket / HTTP / stdio 子进程等）的 Provider **必须**重写此方法做
真实检查：连接可用才返回 ``True``；不可用返回 ``False``；方法内抛异常视作检查
失败，由调用方按不健康处理。

## 可用性动作契约
维护外部连接的 Provider 覆写 ``connect`` / ``disconnect``（无连接语者沿用基类默认，
二者均返回 ``False``）即可启用手动重连。``supports_reconnect`` 属性以"本类是否覆
写了 ``connect``"为单点判定，子类只需覆写 ``connect`` 即自动启用重连支持，**不
需要额外打标。``reconnect`` 默认组合为 ``disconnect`` + ``connect``；维护特殊语
义（如 MCP 关闭整条 stream 后重新建立）的 Provider 可整体覆写。

手动重连的语义在 ``ToolRegistry.reconnect_provider``：重连成功后对归属该 Provider
的全部已熔断工具调用 ``probe_tool`` 探活，通过者即刻 ``recover_tool`` 复位熔断，
联动熔断器恢复路径。手动重连与各 Provider 自身后台重连循环（如 VTS 的
``_reconnect_loop``、Warudo 的 ``_connection_loop``）属低频可接受并发场景，
不强制串行化。

## 简单工具路径（正典）
无状态、轻量的简单工具不必手写 Provider 类：

- ``as_tool_impl``——把普通 async 函数包装成标准工具实现（返回值归一化：
  str/None → content、异常 → 失败结果、自动计时）
- ``make_provider_from_specs``——用一组 ``(spec, impl)`` 构造固定 Provider，
  装配处一行 ``registry.register_provider(...)`` 完成注册

样板见 ``src/modules/memory/query_tool.py``（QueryMemory）。
升级为手写 Provider 类的判据：需要连接重连 / 共享状态 / 动态工具表 / 任务适配器。

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

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec

_logger = get_logger("ToolProvider")


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

    async def connect(self) -> bool:
        """建立通道连接——默认无连接可建，返回 False。

        维护外部连接的 Provider 应覆写：建立成功返回 True，失败返回 False
        （不抛异常）。与 ``BaseToolProvider.connect`` 行为一致；Protocol 上挂此
        签名仅为 duck-typed 完整性保证，runtime_checkable 不会强制所有实现覆写。
        """
        ...

    async def disconnect(self) -> bool:
        """断开通道连接——默认无连接可断，返回 False。

        维护外部连接的 Provider 应覆写：断开完成返回 True，未连接 / 失败返回
        False（不抛异常）。Protocol 签名仅供 duck-typed 完整性保证。
        """
        ...

    async def reconnect(self) -> bool:
        """手动重连——默认组合：先 ``disconnect`` 再 ``connect``。

        子类可整体覆写以表达特殊语义（如关闭底层 stream 后重建）。返回 bool
        等同 ``connect`` 的返回值。Protocol 签名仅供 duck-typed 完整性保证。
        """
        ...

    @property
    def supports_reconnect(self) -> bool:
        """是否支持手动重连——默认按"本类是否覆写了 ``connect``"判定。

        无须子类打标，覆写 ``connect`` 即自动启用。无状态 / 无连接语 Provider
        沿用基类默认（即不支持）。Protocol 上挂此 property 仅为 duck-typed
        完整性保证；运行时判定走 ``BaseToolProvider.supports_reconnect`` 的
        单点实现。
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
    - 连接动作契约默认实现（``connect`` / ``disconnect`` 沿用基类 = 不支持；
      ``reconnect`` = ``disconnect`` + ``connect``）；维护外部连接的 Provider
      覆写 ``connect`` 即可同时获得重连支持，``supports_reconnect`` 按"本类是
      否覆写了 ``connect``"自动判定
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

    async def query_task(self, task_id: str) -> Optional[dict]:
        """查询适配器（可选，默认不支持）：按任务号查执行侧任务快照。

        回执型工具的 provider（如 MCP 游戏 server）覆写本方法，返回
        ``{"status": <词表状态>, "snapshot": {...}, "summary": "..."}``；
        默认 ``None`` 表示不支持查询（跟踪循环跳过核实，仅靠通知/超时）。
        """
        return None

    def subscribe_task_notifications(self, callback) -> Optional[Callable[[], None]]:
        """通知适配器（可选，默认不支持）：订阅执行侧任务提示。

        覆写时返回退订句柄（无参可调用）；收到提示即调 ``callback(task_id)``
        （举旗级、可丢——提示只触发核实，事实以查询为准）。默认 ``None``
        表示不支持订阅（跟踪循环降级为纯周期兜底）。
        """
        return None

    async def health_check(self) -> bool:
        """探活钩子默认实现：返回 True（无可检查之物）。

        维护外部连接（WebSocket / HTTP / stdio 子进程等）的 Provider **必须**
        重写此方法做真实检查（连接可用才返回 True；不可用返回 False）。无状态
        Provider 沿用默认实现——熔断后冷却期满即恢复，再失败再熔断，由流量决定。
        """
        return True

    async def connect(self) -> bool:
        """建立通道连接（默认实现 = 不支持）。

        维护外部连接（WebSocket / HTTP / stdio 子进程等）的 Provider **必须**
        重写此方法：建立成功返回 True，失败返回 False（不抛异常）。无连接
        语 / 无状态 Provider 沿用基类默认——``supports_reconnect`` 据此判定
        为 False，Dashboard 不暴露手动重连按钮。
        """
        return False

    async def disconnect(self) -> bool:
        """断开通道连接（默认实现 = 不支持）。

        与 ``connect`` 对称：维护外部连接的 Provider 重写为真实断开逻辑；无
        连接语 Provider 沿用基类默认。无须与 ``connect`` 同时覆写，但缺一会
        导致 ``reconnect`` 默认组合对缺项方短路失败——重连失败时调用方按返
        回值处理，不上抛。
        """
        return False

    async def reconnect(self) -> bool:
        """手动重连默认组合（先断开再建立）。

        子类整体覆写可表达特殊语义（如 MCP 关闭整条 stream 后重建；OBS 断开
        后通过 ``_connect_obs`` 重建）。返回 bool 等同 ``connect`` 的返回值。
        异常路径不上抛（由 ``connect`` / ``disconnect`` 各自的实现兜底）。
        """
        await self.disconnect()
        return await self.connect()

    @property
    def supports_reconnect(self) -> bool:
        """是否支持手动重连——单点判定：本类是否覆写了 ``connect``。

        通过 ``type(self).connect is not BaseToolProvider.connect`` 内省：不
        依赖子类额外打标，覆写 ``connect`` 即自动启用。``_SpecImplProvider``
        等未覆写 ``connect`` 的子类据此返回 False，与"无状态 Provider 无重连
        按钮"的前端契约一致。
        """
        return type(self).connect is not BaseToolProvider.connect


# =============================================================================
# 返回值归一化：as_tool_impl
# =============================================================================


def as_tool_impl(
    tool_name: str,
    fn: Callable[[ToolInvocation], Awaitable[Any]],
) -> ToolImpl:
    """把普通 async 函数包装成标准工具实现（简单工具正典路径的组成部分）。

    归一化规则：
    - 返回 ``ToolExecutionResult`` → 原样透传（自动补全缺省的时间戳/耗时）
    - 返回 str / None 等普通值 → 成功结果（``content=str(value)``，None → ""）
    - 抛出异常 → 失败结果（``error_message="异常类型: 信息"``，不外抛，
      异常细节记入日志）

    Args:
        tool_name: 工具名（结果的 ``tool_name`` 字段以它回显，保持溯源一致；
            与注册名形态对齐由调用方负责）
        fn: 普通 async 函数，接收 ``ToolInvocation``，返回任意值
    """

    async def _impl(invocation: ToolInvocation) -> ToolExecutionResult:
        started_ms = now_ms()
        try:
            value = await fn(invocation)
        except Exception as exc:  # noqa: BLE001 - 工具边界兜底，异常转失败结果
            finished_ms = now_ms()
            _logger.error(f"工具 '{tool_name}' 执行抛出异常: {type(exc).__name__}: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                timestamp_ms=finished_ms,
                duration_ms=finished_ms - started_ms,
            )
        if isinstance(value, ToolExecutionResult):
            if value.timestamp_ms == 0:
                value.timestamp_ms = now_ms()
            if value.duration_ms == 0:
                value.duration_ms = now_ms() - started_ms
            return value
        finished_ms = now_ms()
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content=str(value) if value is not None else "",
            timestamp_ms=finished_ms,
            duration_ms=finished_ms - started_ms,
        )

    return _impl


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
            # 调用方使用的就是派生全名（<provider>_<工具名>），等值对照分发
            if spec.full_name == invocation.tool_name:
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

    简单工具正典路径：spec + 函数（经 ``as_tool_impl`` 包装）+ 本工厂 +
    装配处一行 ``register_provider``。适用于内置工具（无状态、轻）；
    GameAgent/MCP 推荐手写类实现 ``BaseToolProvider``
    （多状态/多步骤/有外部连接）。
    """
    return _SpecImplProvider(name=name, spec_impl_pairs=spec_impl_pairs, category=category)


__all__ = ["ToolProvider", "BaseToolProvider", "ToolImpl", "as_tool_impl", "make_provider_from_specs"]


# 让 Pylance 不报 _ 变量未用
_ = Any
