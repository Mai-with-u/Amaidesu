"""
ToolRegistry —— 工具注册中心（Wave 3 / §1.5）

- 按名分发工具
- 去重（先注册保留）
- 调用失败兜底（不抛异常，返回失败 ``ToolExecutionResult``）
- 接受 ``ToolProvider`` 整体注册（Provider.list_tools 全量展开）

权威定案：.omo/drafts/amaidesu-v2-architecture.md §1.5 末尾
> 统一由 ToolRegistry：register（去重保留先注册）/ list_tools / invoke
> （异常→error result 兜底）/ to_llm_definitions（内部→LLM 转换层，
> 解耦协议）
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from src.modules.logging import get_logger
from src.modules.tools.models import (
    Provider,
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider

# 工具实现签名：接受 ToolInvocation，返回 await ToolExecutionResult
ToolImplCallable = Callable[[ToolInvocation], Awaitable[ToolExecutionResult]]


logger = get_logger("ToolRegistry")


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

    def __init__(self) -> None:
        # 按 name 索引：首次注册优先（去重）
        self._tools: Dict[str, tuple[ToolSpec, ToolImplCallable]] = {}
        # Provider 引用（仅诊断 / 重复检测）
        self._providers: List[ToolProvider] = []

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
        """注册一个 Provider 的所有工具。返回新注册数（去重不计）。"""
        if provider in self._providers:
            logger.debug(f"Provider '{provider.name}' 已注册过（保留）")
            return 0
        self._providers.append(provider)
        new_count = 0
        for spec in provider.list_tools():
            if self.register(spec, provider.invoke):
                new_count += 1
        logger.info(f"Provider '{provider.name}' 已注册（含 {new_count} 个新工具，总数={len(self._tools)}）")
        return new_count

    # -------------------- 查询 --------------------

    def list_tools(self, provider: Optional[Provider] = None) -> List[ToolSpec]:
        """返回所有已注册工具的 spec。

        Args:
            provider: 可选过滤（"builtin" / "game" / "mcp"）
        """
        specs = [spec for spec, _ in self._tools.values()]
        if provider is not None:
            specs = [s for s in specs if s.provider == provider]
        return specs

    def get(self, name: str) -> Optional[ToolSpec]:
        """按名查 spec。"""
        pair = self._tools.get(name)
        return pair[0] if pair is not None else None

    def has(self, name: str) -> bool:
        """是否存在指定工具。"""
        return name in self._tools

    # -------------------- 调用 --------------------

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """调用工具。永远不抛异常（未知/失败 → 失败 result）。

        注意：实施方返回的已经是 ToolExecutionResult；此处只做包一层 +
        未找到时兜底。
        """
        import time as _time

        pair = self._tools.get(invocation.tool_name)
        if pair is None:
            logger.warning(
                f"ToolRegistry 未找到工具 '{invocation.tool_name}'（source={invocation.source or 'unknown'}）"
            )
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"未知工具: '{invocation.tool_name}'",
                timestamp_ms=int(_time.time() * 1000),
            )
        _spec, impl = pair
        try:
            return await impl(invocation)
        except Exception as exc:  # noqa: BLE001 - 兜底边界
            logger.error(
                f"ToolRegistry 调用工具 '{invocation.tool_name}' 时抛出异常: {exc}",
                exc_info=True,
            )
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                timestamp_ms=int(_time.time() * 1000),
            )

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
        provider: Optional[Provider] = None,
    ) -> List[Dict[str, Any]]:
        """返回 LLM 视角的工具定义列表（OpenAI 风格 function calling 形状）。

        这是与 LLM 协议层之间的转换点（解耦 ToolSpec 与具体 LLM 协议）。
        """
        definitions: List[Dict[str, Any]] = []
        for spec in self.list_tools(provider=provider):
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
        logger.debug("ToolRegistry 已清空")

    # 兼容 inspect / debug
    def __repr__(self) -> str:
        return f"<ToolRegistry tools={len(self._tools)} providers={len(self._providers)}>"


__all__ = ["ToolRegistry", "default_tool_registry", "set_default_registry"]


# Pylance: 暴露 inspect 工具（避免 unused-import 警告）
_ = inspect
