"""
ToolRegistry —— 工具注册中心

- 按名分发工具
- 去重（先注册保留）
- 调用失败兜底（不抛异常，返回失败 ``ToolExecutionResult``）
- 接受 ``ToolProvider`` 整体注册（Provider.list_tools 全量展开）
- 可选挂载 ``EventBus``：每次调用完成后 emit ``tool.result.<name>``，
  供 Dashboard 溯源（broadcaster 通配订阅 ``tool.result.#``）
- 可选熔断器：连续失败计数达阈值则摘除工具（tripped），
  配套 ``ToolHealthMonitor`` 做探活恢复（``src/modules/tools/health.py``）
- 可见名单：``register_provider(visible_to=...)`` 注册处逐工具声明
  可见给哪些 Agent（默认 ``["*"]`` 全员）；``list_tools(for_agent=...)`` 按
  Agent 计算工具列表。名单只约束可见性，``invoke()`` 不校验。

接口约定：register（去重保留先注册）/ register_provider（注册键 = 派生全名 +
提供者单名校验 + 记录 provider 声明的分类 + 可选可见名单）
/ list_tools（for_agent 按名单计算工具列表；provider / category 过滤；运营全集
= 不传 for_agent） / list_categories / invoke（异常→error result 兜底）/
to_llm_definitions（内部→LLM 转换层，解耦协议）/ recover_tool（探活通过后复位熔断）/
probe_tool（按名定位 provider 并调用其 ``health_check`` 拿回 bool）/
visible_to_of（按名查可见名单）。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Iterable, List, Literal, Optional

from src.modules.events.payloads.tool_health import ToolHealthPayload
from src.modules.events.payloads.tool_result import ToolResultPayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import BaseToolProvider, ToolProvider

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus

# 工具实现签名：接受 ToolInvocation，返回 await ToolExecutionResult
ToolImplCallable = Callable[[ToolInvocation], Awaitable[ToolExecutionResult]]


logger = get_logger("ToolRegistry")


# =============================================================================
# 单工具健康状态（熔断器内部数据）
# =============================================================================


@dataclass(slots=True)
class _ToolHealth:
    """单工具熔断器状态（ToolRegistry 内部使用）。

    字段全部为零值起步；invocation 完成后由 ``invoke`` 维护；外部仅通过
    ``tool_health_snapshot`` / ``is_tripped`` / ``recover_tool`` 触达。
    """

    consecutive_failures: int = 0
    tripped: bool = False
    tripped_at_ms: int = 0
    last_error: str = ""


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

    def __init__(
        self,
        event_bus: Optional["EventBus"] = None,
        *,
        failure_threshold: int = 3,
    ) -> None:
        # 按全名索引（全名 = ``spec.full_name`` 派生值，唯一实现见 models.py；
        # 首次注册优先，去重）
        self._tools: Dict[str, tuple[ToolSpec, ToolImplCallable]] = {}
        # Provider 引用（仅诊断 / 重复检测）
        self._providers: List[ToolProvider] = []
        # 提供者名 → 分类（provider 自声明；注册时记录，供 category 查询/过滤）
        self._categories: Dict[str, str] = {}
        # 停用的工具名集合：工具仍保留在注册表中（工具页可见全集），
        # 但对 LLM 不可见（list_tools 默认排除）且调用被拒绝
        self._disabled: set[str] = set()
        # 可选事件总线：挂载后每次调用完成 emit tool.result.<name>
        # 熔断/恢复时额外 emit tool.health.<name>
        self._event_bus = event_bus
        # 单工具熔断器状态（仅在 invoke 触发失败/成功后维护）
        self._health: Dict[str, _ToolHealth] = {}
        # 连续失败达阈值即熔断（<=0 关闭熔断：状态仍记录、绝不跳闸）
        self._failure_threshold = failure_threshold
        # 注册名 → 所属 BaseToolProvider 实例（register_provider 时记录；
        # 探活按此直查归属，避免 provider.name 与 spec.provider 的字符串耦合）
        self._tool_owner: Dict[str, BaseToolProvider] = {}
        # 注册名 → 可见名单（register_provider 的 visible_to 声明；未声明的
        # 工具不在表中，等价 ["*"] 全员可见）。名单是生产侧代码事实：
        # 值为 Agent 注册名列表或 ["*"]；只约束可见性（for_agent 计算），
        # invoke 不校验（编名直调是已知边界）。
        self._visible_to: Dict[str, List[str]] = {}

    # -------------------- 注册 --------------------

    def register(self, spec: ToolSpec, impl: ToolImplCallable) -> bool:
        """注册一个工具（先注册保留）。索引键 = ``spec.full_name``（派生值）。

        Returns:
            是否成功注册（重复全名 → 跳过并返回 False）
        """
        registered_name = spec.full_name
        existing = self._tools.get(registered_name)
        if existing is not None:
            logger.debug(f"工具 '{registered_name}' 已注册（保留先注册，跳过本次 provider={spec.provider}）")
            return False
        self._tools[registered_name] = (spec, impl)
        logger.debug(f"工具 '{registered_name}' 已注册（provider={spec.provider}, kind={spec.kind}）")
        return True

    def register_provider(
        self,
        provider: ToolProvider,
        *,
        visible_to: Optional[Dict[str, List[str]]] = None,
    ) -> int:
        """注册一个 Provider 的全部工具。返回新注册数（去重不计）。

        命名模型：注册键 = 每个 spec 的派生全名（``<provider>_<工具名>``，
        唯一实现见 ``ToolSpec.full_name``）；spec **原样存储**（声明名保持
        裸名，不做任何改名拷贝）。provider 自声明的 ``category`` 一并记录
        （按 spec.provider 提供者名归组，供 ``list_categories()`` /
        ``list_tools(category=)`` 查询）。

        提供者单名校验（fail-fast）：Provider 的 ``name`` 必须与其全部
        spec 的 ``provider`` 同值（同值同源）——不一致直接抛 ``ValueError``。
        这是"一个提供者一个短名"的注册期保证；探活/关闭/归属仍按对象
        引用（``_tool_owner``）工作，不依赖字符串。

        可见名单（``visible_to``，生产侧声明）：键 = 本次注册项声明的**工具
        全名**，值 = 可见的 Agent 注册名列表或 ``["*"]``（全员）。校验
        fail-fast：值非空且元素为非空字符串、``"*"`` 只能单独出现、键必须
        命中本注册项声明的工具全名（拼错即报错）。**未列出的工具默认
        ``["*"]``**（共享常态，全局注册零负担）。名单只约束可见性
        （``list_tools(for_agent=...)`` 按它计算工具列表）；``invoke()``
        不校验——LLM 幻觉编名直调保留工具是已知的受众治理边界。

        迁移完整性提示：传入对象非 ``BaseToolProvider`` 子类时记 WARNING
        （每次注册都记——迁移未完成的持续信号，提示补齐 BaseToolProvider 继承）。
        仍照常注册（向后兼容，不抛错）。
        """
        if provider in self._providers:
            logger.debug(f"Provider '{provider.name}' 已注册过（保留）")
            return 0
        specs = list(provider.list_tools())
        mismatched = [s for s in specs if s.provider != provider.name]
        if mismatched:
            detail = ", ".join(f"spec({s.provider!r}, {s.name!r})" for s in mismatched)
            raise ValueError(
                f"Provider 名与其 spec.provider 必须同值同源：provider.name={provider.name!r} "
                f"但声明了不同的 provider 值 [{detail}]"
            )
        declared_full_names = {s.full_name for s in specs}
        validated_lists = self._validate_visible_to(visible_to, declared_full_names)
        if not isinstance(provider, BaseToolProvider):
            logger.warning(
                f"Provider '{provider.name}'（class={type(provider).__name__}）"
                "非 BaseToolProvider 子类，属迁移遗留，无法参与探活约定；"
                "请继承 BaseToolProvider 并按需覆写 health_check"
            )
        self._providers.append(provider)
        category = getattr(provider, "category", "") or ""
        new_count = 0
        for spec in specs:
            registered_name = spec.full_name
            self._record_category(spec.provider, category)
            if self.register(spec, provider.invoke):
                new_count += 1
            # 记录"全名 → Provider 实例"所有权：探活按此直查归属
            # （provider.name 与 spec.provider 同值同源，见注册校验）
            if isinstance(provider, BaseToolProvider):
                self._tool_owner[registered_name] = provider
            if validated_lists is not None:
                self._visible_to[registered_name] = validated_lists[registered_name]
        logger.info(f"Provider '{provider.name}' 已注册（含 {new_count} 个新工具，总数={len(self._tools)}）")
        return new_count

    @staticmethod
    def _validate_visible_to(
        visible_to: Optional[Dict[str, List[str]]],
        declared_full_names: set[str],
    ) -> Optional[Dict[str, List[str]]]:
        """校验可见名单并按全名补全（未列工具填默认 ``["*"]``）；非法即抛错。

        校验规则（fail-fast）：
        - 值必须是非空列表，元素为非空字符串（Agent 注册名）
        - ``"*"`` 表示全员，只能单独出现（``["*", "x"]`` 非法）
        - 键必须命中该注册项声明的工具全名（拼错即报错，防名单静默失效）
        """
        if visible_to is None:
            return None
        result: Dict[str, List[str]] = {}
        unknown_keys = [k for k in visible_to if k not in declared_full_names]
        if unknown_keys:
            raise ValueError(
                f"visible_to 中的键未命中本注册项声明的工具全名（可能拼错）: "
                f"{sorted(unknown_keys)}；本注册项声明: {sorted(declared_full_names)}"
            )
        for full_name in declared_full_names:
            entries = visible_to.get(full_name, ["*"])
            if not isinstance(entries, list) or not entries:
                raise ValueError(f"visible_to['{full_name}'] 必须是非空列表，得到 {entries!r}")
            if any((not isinstance(e, str)) or (not e) for e in entries):
                raise ValueError(f"visible_to['{full_name}'] 的元素必须是非空字符串，得到 {entries!r}")
            if "*" in entries and len(entries) > 1:
                raise ValueError(f"visible_to['{full_name}'] 含 '*' 时只能单独出现（全员），得到 {entries!r}")
            result[full_name] = list(entries)
        return result

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
        include_tripped: bool = False,
        for_agent: Optional[str] = None,
        include_scoped: bool = False,
    ) -> List[ToolSpec]:
        """返回已注册工具的 spec（默认排除停用/熔断工具）。

        可见性按名单计算：
        - ``for_agent=None`` → 不做名单过滤（运营全集；Dashboard 工具页用）
        - ``for_agent="<Agent 注册名>"`` → 只返回名单包含该名或 ``["*"]``
          的工具（该 Agent 的工具列表；全体消费方统一从这里拿）

        Args:
            provider: 可选过滤（提供者标识，如 "vts" / "warudo" /
                "obs" / "vision" / "memory" / "maicraft"）；None 返回全部。
            category: 可选过滤（提供者自声明的分类，如 "avatar" /
                "studio" / "game"）；None 不按分类过滤。
            include_disabled: True 时包含已停用工具（工具页展示全集用）；
                LLM 可见性（Planner / Replyer / to_llm_definitions）走默认
                排除路径。
            include_tripped: True 时包含熔断中的工具（Dashboard 工具页展示全集用）。
                默认排除以避免 LLM 看见已被摘除的工具。
            for_agent: 按 Agent 注册名计算工具列表（见上）；None 为运营全集。
            include_scoped: **已废弃，无效果**（保留形参兼容旧调用方；
                名单机制下全集即默认行为）。

        Returns:
            满足条件的 spec 列表（过滤条件为 AND 关系）。
        """
        specs = [spec for spec, _ in self._tools.values()]
        if not include_disabled:
            specs = [s for s in specs if s.full_name not in self._disabled]
        if not include_tripped:
            specs = [s for s in specs if not self.is_tripped(s.full_name)]
        if for_agent is not None:
            specs = [s for s in specs if self._is_visible_to(s.full_name, for_agent)]
        if provider is not None:
            specs = [s for s in specs if s.provider == provider]
        if category is not None:
            specs = [s for s in specs if self._categories.get(s.provider) == category]
        return specs

    def _is_visible_to(self, full_name: str, agent_name: str) -> bool:
        """名单判定：未声明（不在表中）= 全员可见；声明则须命中该 Agent 名或 "*"。"""
        entries = self._visible_to.get(full_name)
        if entries is None:
            return True
        return "*" in entries or agent_name in entries

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

    def visible_to_of(self, full_name: str) -> List[str]:
        """返回工具的可见名单（未声明 = 全员，返回 ``["*"]`` 快照）。

        供运营面（Dashboard 等）标注"这个工具谁能看见"。名单只约束
        可见性，``invoke()`` 不校验（编名直调是已知边界）。
        """
        entries = self._visible_to.get(full_name)
        return list(entries) if entries is not None else ["*"]

    # -------------------- 停用 --------------------

    def apply_disabled(self, names: Iterable[str]) -> int:
        """整体设置停用集合（组合根装配完成后调用）。

        未匹配任何已注册工具的条目记一行 warning 并列出（防止工具改名后
        配置里的停用条目静默失效）；已存在的名字正常生效。

        Returns:
            实际生效的停用工具数（即注册表中存在的名字数）。
        """
        name_set = set(names)
        self._disabled = {n for n in name_set if n in self._tools}
        unmatched = sorted(name_set - self._disabled)
        if unmatched:
            logger.warning(f"停用列表中有 {len(unmatched)} 个未注册工具（可能已改名或拼错，本次不生效）: {unmatched}")
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

        熔断器联动：
        - 未知/停用短路返回 **不** 触达健康状态（与旧契约一致）
        - 熔断短路返回 **不** emit ``tool.result.<name>``（与未知/停用短路一致）
        - 实施方成功 → 重置 ``consecutive_failures`` 为 0
        - 实施方失败（含抛异常）→ ``consecutive_failures += 1``、记 ``last_error``，
          达到阈值且未跳闸 → 标记 tripped、emit ``tool.health.<name>``（state=open）
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
        if self.is_tripped(invocation.tool_name):
            logger.warning(f"工具 '{invocation.tool_name}' 已熔断，拒绝调用（source={invocation.source or 'unknown'}）")
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message="工具已熔断，探活恢复中",
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
        await self._record_health(spec, result)
        await self._emit_tool_result(spec, result, invocation)
        return result

    async def _emit_tool_result(self, spec: ToolSpec, result: ToolExecutionResult, invocation: ToolInvocation) -> None:
        """广播工具结果事件（``tool.result.<name>``）；任何失败仅记日志。

        调用上下文（round_id / caller_source）从 invocation 透传进 payload，
        供观察器区分工具调用的归属轮次与 Agent。
        """
        if self._event_bus is None:
            return
        try:
            if isinstance(result.structured_content, dict):
                result_data: Dict[str, Any] = result.structured_content
            else:
                result_data = {"content": result.content}
            payload = ToolResultPayload(
                tool_name=result.tool_name,
                round_id=invocation.round_id or None,
                caller_source=invocation.source or None,
                status="success" if result.success else "error",
                arguments=dict(invocation.arguments or {}),
                result=result_data,
                error_message=result.error_message,
                timestamp_ms=result.timestamp_ms or int(time.time() * 1000),
            )
            await self._event_bus.emit(spec.resolve_result_event(), payload, source="ToolRegistry")
        except Exception as exc:  # noqa: BLE001 - 观测旁路，不反噬调用方
            logger.warning(f"tool.result 事件广播失败（工具: {spec.full_name}）: {exc}")

    # -------------------- 熔断器：内部维护 --------------------

    def _ensure_health(self, name: str) -> _ToolHealth:
        """懒初始化健康状态（首次失败/恢复前不存在）。"""
        health = self._health.get(name)
        if health is None:
            health = _ToolHealth()
            self._health[name] = health
        return health

    async def _record_health(self, spec: ToolSpec, result: ToolExecutionResult) -> None:
        """在 ``invoke`` 主路径上更新单工具熔断器状态。

        - 成功 → 重置连续失败
        - 失败 → 递增计数、记录最近错误；达到阈值且未跳闸 → 触发熔断并广播
        """
        health = self._ensure_health(spec.full_name)
        if result.success:
            health.consecutive_failures = 0
            health.last_error = ""
            return
        health.consecutive_failures += 1
        health.last_error = result.error_message or ""
        threshold = self._failure_threshold
        if threshold > 0 and not health.tripped and health.consecutive_failures >= threshold:
            health.tripped = True
            health.tripped_at_ms = now_ms()
            logger.warning(
                f"工具 '{spec.full_name}' 连续失败 {health.consecutive_failures} 次（阈值 {threshold}），"
                "已熔断摘除，等待探活恢复"
            )
            await self._emit_tool_health(
                spec.full_name,
                spec.provider,
                state="open",
                failure_count=health.consecutive_failures,
                last_error=health.last_error,
            )

    async def _emit_tool_health(
        self,
        tool_name: str,
        provider: str,
        *,
        state: Literal["open", "closed"],
        failure_count: int,
        last_error: str,
    ) -> None:
        """广播工具健康切换事件（``tool.health.<name>``）；事件总线缺失或广播失败仅记日志。"""
        if self._event_bus is None:
            return
        try:
            payload = ToolHealthPayload(
                tool_name=tool_name,
                provider=provider,
                state=state,
                failure_count=failure_count,
                last_error=last_error,
            )
            await self._event_bus.emit(
                f"tool.health.{tool_name}",
                payload,
                source="ToolRegistry",
            )
        except Exception as exc:  # noqa: BLE001 - 观测旁路
            logger.warning(f"tool.health 事件广播失败（工具: {tool_name}）: {exc}")

    # -------------------- 熔断器：公共 API --------------------

    def is_tripped(self, name: str) -> bool:
        """工具是否处于熔断状态。"""
        health = self._health.get(name)
        return bool(health and health.tripped)

    @property
    def tripped_tools(self) -> List[str]:
        """当前熔断中的工具名（排序快照，供 ToolHealthMonitor 遍历）。"""
        return sorted(name for name, h in self._health.items() if h.tripped)

    def tool_health_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """返回对 Dashboard 有意义的工具健康快照。

        仅包含熔断中或近期有失败的工具；其余工具按 "健康" 处理（Dashboard
        按缺席键推断）。键为注册名，值包含 provider/state/failure_count/
        last_error/tripped_at_ms 字段。
        """
        snapshot: Dict[str, Dict[str, Any]] = {}
        for name, health in self._health.items():
            if not health.tripped and health.consecutive_failures <= 0:
                continue
            spec = self._tools.get(name)
            snapshot[name] = {
                "provider": spec[0].provider if spec is not None else "",
                "state": "tripped" if health.tripped else "healthy",
                "failure_count": health.consecutive_failures,
                "last_error": health.last_error,
                "tripped_at_ms": health.tripped_at_ms,
            }
        return snapshot

    def recover_tool(self, name: str) -> bool:
        """手动/探活通过时复位熔断器；返回是否真做了恢复动作。

        同步语义：状态在调用瞬间翻转；``tool.health.<name>`` 切换事件通过
        ``asyncio.create_task`` 后台调度（调用方在 async 上下文时生效，
        无运行循环时静默丢弃——此情形下事件消费方也不会启动）。
        """
        health = self._health.get(name)
        if health is None or not health.tripped:
            return False
        health.consecutive_failures = 0
        health.tripped = False
        health.tripped_at_ms = 0
        health.last_error = ""
        spec = self._tools.get(name)
        provider_id = spec[0].provider if spec is not None else ""
        logger.info(f"工具 '{name}' 已恢复可用（探活通过）")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无运行循环：事件总线也无法调度；状态已翻转即可
            return True
        loop.create_task(
            self._emit_tool_health(
                name,
                provider_id,
                state="closed",
                failure_count=0,
                last_error="",
            )
        )
        return True

    async def probe_tool(self, tool_name: str) -> bool:
        """对指定工具调用其所属 provider 的 ``health_check``。

        归属解析走 ``_tool_owner``（注册时记录的"全名 → Provider 实例"
        映射），按对象引用直查，不做名字字符串匹配（provider.name 与
        spec.provider 同值同源，见 ``register_provider`` 校验）。

        工具未经 ``register_provider`` 注册（如 ``register()`` 直注册的
        裸函数）时无归属 provider，按基类默认语义返回 True——"无可检查
        之物，让流量决定"：熔断后冷却期满即恢复，再失败再熔断。

        探活过程异常按不健康处理（warning 日志携带类型 + 消息，返回 False）。

        Returns:
            True = 当前可用（或无归属 provider、无检查必要）；
            False = 不可用或探活异常。
        """
        owner = self._tool_owner.get(tool_name)
        if owner is None:
            return True
        try:
            return bool(await owner.health_check())
        except Exception as exc:  # noqa: BLE001 - 探活异常=不健康，不上抛
            logger.warning(
                f"probe_tool: provider '{owner.name}' health_check 异常（{type(exc).__name__}: {exc}），按不健康处理"
            )
            return False

    def _tools_owned_by(self, provider: ToolProvider) -> List[str]:
        """列出归属指定 Provider 的全部已注册工具名（按 ``_tool_owner`` 反查）。

        复用 ``_tool_owner`` 反向索引：O(N) 单次扫描，未额外维护反向表——
        注册表量级（数百）下单次扫描成本可忽略，避免给热路径加状态。
        用于 ``reconnect_provider`` 在重连成功后遍历归属工具做探活 + 熔断复位。
        """
        return [name for name, owner in self._tool_owner.items() if owner is provider]

    def provider_of_tool(self, full_name: str) -> Optional[ToolProvider]:
        """按工具全名查归属 Provider 实例（``_tool_owner`` 直查；未知返回 None）。

        供任务跟踪循环定位受理工具的执行侧（查询/通知适配器挂在该
        provider 上）；与 ``probe_tool`` 的归属解析同口径。
        """
        return self._tool_owner.get(full_name)

    def provider_supports_reconnect(self, tool_name: str) -> bool:
        """按工具名查归属 Provider 是否支持手动重连（供 Dashboard 工具页渲染按钮用）。

        无归属 Provider / 归属对象非 ``BaseToolProvider`` / Provider 的
        ``supports_reconnect`` 为 False 任一条件触发即返回 False。未知工具名
        同样 False）。不解名/字符串猜测，按 ``_tool_owner`` 直查——与
        ``probe_tool`` 的归属解析口径一致。
        """
        owner = self._tool_owner.get(tool_name)
        if not isinstance(owner, BaseToolProvider):
            return False
        return bool(owner.supports_reconnect)

    async def reconnect_provider(self, provider_id: str) -> Dict[str, Any]:
        """手动重连指定 Provider；成功后对归属其工具的熔断器联动探活复位。

        行为：
        - ``provider_id`` = Provider.name（按 ``_providers`` 线性查找）。
          provider.name 与 spec.provider 同值同源（注册期校验保证），外部
          （Dashboard 等）传提供者短名即可命中。
        - 未找到 → ``{"ok": False, "error": "未注册 Provider: <id>"}``
        - 找到但不支持重连（非 BaseToolProvider 或 ``supports_reconnect``
          为 False）→ ``{"ok": False, "error": "Provider ... 不支持手动重连"}``
        - 找到且支持 → ``await provider.reconnect()``：
          - 失败 → ``{"ok": False, "error": "重连失败...", "provider_id": <id>}``
          - 成功 → 反查归属工具，逐个 ``probe_tool``，通过且 ``is_tripped`` 即
            ``recover_tool``；返回 ``{"ok": True, "provider_id": ..., "recovered":
            [<已复位工具名>], "still_tripped": [<探活未通过的熔断工具名>]}``。
            未熔断的工具不纳入报告（运营只需关心"恢复 + 仍未恢复"两个集合）。
        """
        provider = next((p for p in self._providers if p.name == provider_id), None)
        if provider is None:
            logger.warning(f"手动重连失败：未注册 Provider '{provider_id}'")
            return {"ok": False, "error": f"未注册 Provider: {provider_id}"}
        if not isinstance(provider, BaseToolProvider) or not provider.supports_reconnect:
            logger.warning(
                f"手动重连失败：Provider '{provider_id}'（class={type(provider).__name__}）"
                "不支持手动重连（无覆写 connect 或非 BaseToolProvider）"
            )
            return {
                "ok": False,
                "error": f"Provider '{provider_id}' 不支持手动重连",
            }

        logger.info(f"手动触发 Provider '{provider_id}' 重连")
        try:
            ok = bool(await provider.reconnect())
        except Exception as exc:  # noqa: BLE001 - 重连边界兜底，不上抛
            logger.error(
                f"Provider '{provider_id}' 重连异常: {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            return {
                "ok": False,
                "error": f"重连失败: {type(exc).__name__}: {exc}",
                "provider_id": provider_id,
            }
        if not ok:
            logger.warning(f"Provider '{provider_id}' 手动重连失败（provider.reconnect 返回 False）")
            return {
                "ok": False,
                "error": "重连失败: provider.reconnect 返回 False",
                "provider_id": provider_id,
            }

        recovered: List[str] = []
        still_tripped: List[str] = []
        for tool_name in self._tools_owned_by(provider):
            if not self.is_tripped(tool_name):
                continue
            healthy = await self.probe_tool(tool_name)
            if healthy and self.recover_tool(tool_name):
                recovered.append(tool_name)
            else:
                still_tripped.append(tool_name)
        logger.info(
            f"Provider '{provider_id}' 重连成功，恢复 {len(recovered)} 个熔断工具"
            + (f"，仍有 {len(still_tripped)} 个未通过探活" if still_tripped else "")
        )
        return {
            "ok": True,
            "provider_id": provider_id,
            "recovered": recovered,
            "still_tripped": still_tripped,
        }

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
                "name": spec.full_name,
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
        self._health.clear()
        self._tool_owner.clear()
        self._visible_to.clear()
        logger.debug("ToolRegistry 已清空")

    # 兼容 inspect / debug
    def __repr__(self) -> str:
        tripped = len(self.tripped_tools)
        return f"<ToolRegistry tools={len(self._tools)} providers={len(self._providers)} tripped={tripped}>"


__all__ = ["ToolRegistry", "default_tool_registry", "set_default_registry"]


# Pylance: 暴露 inspect 工具（避免 unused-import 警告）
_ = inspect
