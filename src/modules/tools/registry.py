"""
ToolRegistry —— 工具注册中心

- 按名分发工具
- 去重（先注册保留）
- 调用失败兜底（不抛异常，返回失败 ``ToolExecutionResult``）
- 接受 ``ToolProvider`` 整体注册（Provider.list_tools 全量展开）
- 可选挂载 ``EventBus``：每次调用完成后 emit ``tool.result.<name>``，
  供 Dashboard 溯源（broadcaster 通配订阅 ``tool.result.#``）

接口约定：register（去重保留先注册）/ register_provider（注册名统一
``<provider>_<工具名>`` 前缀 + 记录 provider 声明的分类）/ list_tools /
list_categories / invoke（异常→error result 兜底）/ to_llm_definitions
（内部→LLM 转换层，解耦协议）
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Iterable, List, Optional

from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus

# 工具实现签名：接受 ToolInvocation，返回 await ToolExecutionResult
ToolImplCallable = Callable[[ToolInvocation], Awaitable[ToolExecutionResult]]


logger = get_logger("ToolRegistry")


def _resolve_registered_name(spec: ToolSpec) -> str:
    """把声明名解析为注册名（``<provider>_<name>``，无条件）。

    注册名规则：``注册名 = <provider>_<工具名>``。provider 是全局唯一的
    提供者名、工具名 provider 内唯一，二者拼出的注册名全局唯一，且满足
    OpenAI / Anthropic function calling 对工具名字符集的要求（分隔符 `_`，
    不用 `:`）。spec.name 已带 ``<provider>_`` 前缀时原样返回（存量 provider
    对齐后常见）；未带则补前缀。provider 为空（匿名工具）时不做改写。
    """
    if not spec.provider:
        return spec.name
    prefix = f"{spec.provider}_"
    if spec.name.startswith(prefix):
        return spec.name
    return f"{prefix}{spec.name}"


# =============================================================================
# 单例：默认全局注册器
# =============================================================================


_default_registry: Optional["ToolRegistry"] = None


def default_tool_registry() -> "ToolRegistry":
    """获取/创建默认注册器（进程内单例）。

    .. warning::
        **仅用于测试兼容**——生产代码禁止使用。

    生产路径应：
    1. 由组合根显式构造 ``ToolRegistry()`` 实例
    2. 调用 ``bind_core_tools(registry, config)`` 注入 L2 Provider
    3. 调用 ``bind_pending_tools(registry)`` 刷入 L1 ``@tool`` 装饰的工具

    该单例仅保留以兼容依赖 ``default_tool_registry()`` 的旧测试；
    ``set_default_registry(None)`` 可用于测试间重置隔离。
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry


def set_default_registry(registry: Optional["ToolRegistry"]) -> None:
    """设置/清除默认注册器。

    .. warning::
        **仅用于测试兼容**——生产代码禁止使用。

    仅供测试在用例间重置默认注册器以保证隔离；
    生产路径应在组合根构造专属 registry，不依赖全局单例。
    """
    global _default_registry
    _default_registry = registry


# =============================================================================
# ToolRegistry 实现
# =============================================================================


class ToolRegistry:
    """工具注册中心。"""

    def __init__(self, event_bus: Optional["EventBus"] = None) -> None:
        # 按 name 索引：首次注册优先（去重）
        self._tools: Dict[str, tuple[ToolSpec, ToolImplCallable]] = {}
        # Provider 引用（仅诊断 / 重复检测）
        self._providers: List[ToolProvider] = []
        # 提供者名 → 分类（provider 自声明；注册时记录，供 category 查询/过滤）
        self._categories: Dict[str, str] = {}
        # 停用的工具名集合：工具仍保留在注册表中（工具页可见全集），
        # 但对 LLM 不可见（list_tools 默认排除）且调用被拒绝
        self._disabled: set[str] = set()
        # 可选事件总线：挂载后每次调用完成 emit tool.result.<name>
        self._event_bus = event_bus

    # -------------------- 注册 --------------------

    def register(self, spec: ToolSpec, impl: ToolImplCallable) -> bool:
        """注册一个工具（先注册保留）。

        Returns:
            是否成功注册（重复 name → 跳过并返回 False）
        """
        existing = self._tools.get(spec.name)
        if existing is not None:
            logger.debug(f"工具 '{spec.name}' 已注册（保留先注册，跳过本次 provider={spec.provider}）")
            return False
        self._tools[spec.name] = (spec, impl)
        logger.debug(f"工具 '{spec.name}' 已注册（provider={spec.provider}, kind={spec.kind}）")
        return True

    def register_tool(self, spec: ToolSpec, impl: ToolImplCallable) -> bool:
        """``register`` 的别名。"""
        return self.register(spec, impl)

    def register_provider(self, provider: ToolProvider) -> int:
        """注册一个 Provider 的所有工具。返回新注册数（去重不计）。

        注册名统一改写为 ``<provider>_<工具名>``（规则见
        ``_resolve_registered_name``）：spec 未带前缀时用 ``dataclasses.replace``
        拷贝改写 name 后再注册，**不污染** provider ``list_tools()`` 返回的
        原 spec 对象。provider 声明的 ``category`` 一并记录（按 spec.provider
        提供者名归组，供 ``list_categories()`` / ``list_tools(category=)`` 查询）。
        """
        if provider in self._providers:
            logger.debug(f"Provider '{provider.name}' 已注册过（保留）")
            return 0
        self._providers.append(provider)
        category = getattr(provider, "category", "") or ""
        new_count = 0
        for spec in provider.list_tools():
            registered_name = _resolve_registered_name(spec)
            reg_spec = spec
            if registered_name != spec.name:
                # 拷贝改写，避免把前缀写回 provider 原 spec（list_tools 可重复调用）
                reg_spec = replace(spec, name=registered_name)
            self._record_category(spec.provider, category)
            if self.register(reg_spec, provider.invoke):
                new_count += 1
        logger.info(f"Provider '{provider.name}' 已注册（含 {new_count} 个新工具，总数={len(self._tools)}）")
        return new_count

    def _record_category(self, provider_name: str, category: str) -> None:
        """记录"提供者名 → 分类"映射（先注册保留，冲突仅记日志）。"""
        if not provider_name or not category:
            return
        prev = self._categories.get(provider_name)
        if prev is None:
            self._categories[provider_name] = category
        elif prev != category:
            logger.warning(f"提供者 '{provider_name}' 分类冲突：已记录 '{prev}'，忽略新声明 '{category}'")

    # -------------------- 查询 --------------------

    def list_tools(
        self,
        provider: Optional[str] = None,
        category: Optional[str] = None,
        *,
        include_disabled: bool = False,
    ) -> List[ToolSpec]:
        """返回已注册工具的 spec（默认排除停用工具）。

        Args:
            provider: 可选过滤（提供者标识，如 "vts" / "warudo" /
                "obs" / "vision" / "memory" / "maicraft"）；None 返回全部。
            category: 可选过滤（提供者自声明的分类，如 "avatar" /
                "studio" / "game"）；None 不按分类过滤。
            include_disabled: True 时包含已停用工具（工具页展示全集用）；
                LLM 可见性（Planner / Replyer / to_llm_definitions）走默认
                排除路径。

        Returns:
            满足条件的 spec 列表（过滤条件为 AND 关系）。
        """
        specs = [spec for spec, _ in self._tools.values()]
        if not include_disabled:
            specs = [s for s in specs if s.name not in self._disabled]
        if provider is not None:
            specs = [s for s in specs if s.provider == provider]
        if category is not None:
            specs = [s for s in specs if self._categories.get(s.provider) == category]
        return specs

    def list_categories(self) -> List[str]:
        """返回当前已注册提供者声明的全部分类（去重、按名排序）。

        分类由 provider 注册时自声明（ToolProvider.category）；本方法暴露
        给后端查询（如 Dashboard 工具管理页按分类分组）。
        """
        return sorted({c for c in self._categories.values() if c})

    def get(self, name: str) -> Optional[ToolSpec]:
        """按名查 spec。"""
        pair = self._tools.get(name)
        return pair[0] if pair is not None else None

    def has(self, name: str) -> bool:
        """是否存在指定工具。"""
        return name in self._tools

    def category_of(self, name: str) -> str:
        """返回工具所属提供者声明的分类（未知工具 / 未声明返回空串）。"""
        spec = self.get(name)
        if spec is None:
            return ""
        return self._categories.get(spec.provider, "")

    # -------------------- 停用 --------------------

    def apply_disabled(self, names: Iterable[str]) -> int:
        """整体设置停用集合（组合根装配完成后调用；未知名字忽略）。

        Returns:
            实际生效的停用工具数（即注册表中存在的名字数）。
        """
        self._disabled = {n for n in names if n in self._tools}
        if self._disabled:
            logger.info(f"ToolRegistry 已停用 {len(self._disabled)} 个工具: {sorted(self._disabled)}")
        return len(self._disabled)

    def is_disabled(self, name: str) -> bool:
        """工具是否处于停用状态。"""
        return name in self._disabled

    @property
    def disabled_tools(self) -> List[str]:
        """当前停用的工具名（排序后快照）。"""
        return sorted(self._disabled)

    # -------------------- 调用 --------------------

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """调用工具。永远不抛异常（未知/失败 → 失败 result）。

        注意：实施方返回的已经是 ToolExecutionResult；此处只做包一层 +
        未找到时兜底。挂载了 EventBus 时，找到工具并执行完成（无论成败）
        都会 emit ``tool.result.<name>``；emit 失败不影响调用结果。
        """
        pair = self._tools.get(invocation.tool_name)
        if pair is None:
            logger.warning(
                f"ToolRegistry 未找到工具 '{invocation.tool_name}'（source={invocation.source or 'unknown'}）"
            )
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"未知工具: '{invocation.tool_name}'",
                timestamp_ms=int(time.time() * 1000),
            )
        if invocation.tool_name in self._disabled:
            logger.warning(f"工具 '{invocation.tool_name}' 已停用，拒绝调用（source={invocation.source or 'unknown'}）")
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"工具 '{invocation.tool_name}' 已停用（可在 Web UI 工具页重新启用）",
                timestamp_ms=int(time.time() * 1000),
            )
        spec, impl = pair
        try:
            result = await impl(invocation)
        except Exception as exc:  # noqa: BLE001 - 兜底边界
            logger.error(
                f"ToolRegistry 调用工具 '{invocation.tool_name}' 时抛出异常: {exc}",
                exc_info=True,
            )
            result = ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                timestamp_ms=int(time.time() * 1000),
            )
        await self._emit_tool_result(spec, result)
        return result

    async def _emit_tool_result(self, spec: ToolSpec, result: ToolExecutionResult) -> None:
        """广播工具结果事件（``tool.result.<name>``）；任何失败仅记日志。"""
        if self._event_bus is None:
            return
        try:
            if isinstance(result.structured_content, dict):
                result_data: Dict[str, Any] = result.structured_content
            else:
                result_data = {"content": result.content}
            payload = ToolResultPayload(
                tool_name=result.tool_name,
                status="success" if result.success else "error",
                result=result_data,
                error_message=result.error_message,
                timestamp_ms=result.timestamp_ms or int(time.time() * 1000),
            )
            await self._event_bus.emit(spec.resolve_result_event(), payload, source="ToolRegistry")
        except Exception as exc:  # noqa: BLE001 - 观测旁路，不反噬调用方
            logger.warning(f"tool.result 事件广播失败（工具: {spec.name}）: {exc}")

    async def invoke_many(
        self,
        invocations: Iterable[ToolInvocation],
    ) -> List[ToolExecutionResult]:
        """批量调用（gather 并发）。"""
        invs = list(invocations)
        if not invs:
            return []
        return await asyncio.gather(*[self.invoke(inv) for inv in invs])

    # -------------------- LLM 透传 --------------------

    def to_llm_definitions(
        self,
        *,
        provider: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """返回 LLM 视角的工具定义列表（OpenAI 风格 function calling 形状）。

        这是与 LLM 协议层之间的转换点（解耦 ToolSpec 与具体 LLM 协议）。
        """
        definitions: List[Dict[str, Any]] = []
        for spec in self.list_tools(provider=provider, category=category):
            entry: Dict[str, Any] = {
                "name": spec.name,
                "description": spec.description,
            }
            if spec.parameters_schema is not None:
                entry["parameters"] = spec.parameters_schema
            definitions.append(entry)
        return definitions

    # -------------------- 元信息 --------------------

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def clear(self) -> None:
        """清空所有注册（主要用于测试）。"""
        self._tools.clear()
        self._providers.clear()
        self._categories.clear()
        self._disabled.clear()
        logger.debug("ToolRegistry 已清空")

    # 兼容 inspect / debug
    def __repr__(self) -> str:
        return f"<ToolRegistry tools={len(self._tools)} providers={len(self._providers)}>"


__all__ = ["ToolRegistry", "default_tool_registry", "set_default_registry"]


# Pylance: 暴露 inspect 工具（避免 unused-import 警告）
_ = inspect
