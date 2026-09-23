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
  可见给哪些 Agent（默认 ``DEFAULT_VISIBLE_TO``，仅主播）；``list_tools(for_agent=...)`` 按
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
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Iterable, List, Literal, Optional, Union

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

# 可见名单来源：静态名单 dict，或按工具集重新派生名单的策略 callable。
# fail-closed 名单必须走策略形态——工具集刷新时对新工具重跑派生，
# 否则新工具落默认名单，会对预期外的 Agent 泄露可见性。
VisibleToSource = Union[Dict[str, List[str]], Callable[[List[ToolSpec]], Dict[str, List[str]]]]

# 可见名单默认值：未显式声明 visible_to 的工具只对主播可见（fail-closed）。
# 主播 Agent 是本项目核心、非热插拔（框架级事实，同 agents/factory 的
# SUPPORTED_AGENTS 常量）；绝大多数工具只属主播，特殊共享需求
# （如 vision_look_at_screen）由注册处显式声明 ``["*"]``。
DEFAULT_VISIBLE_TO: List[str] = ["streamer"]


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
        # 已声明停用但尚未注册的工具名（[tools].disabled_tools 中指向
        # 降级登记 Provider 的条目，如连接失败时的 vts_*）：工具日后经
        # register 进场时自动转正为停用，配置声明不因注册时序而失灵
        self._disabled_pending: set[str] = set()
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
        # 工具不在表中，按 DEFAULT_VISIBLE_TO 默认名单计算可见性）。名单是
        # 生产侧代码事实：值为 Agent 注册名列表或 ["*"]；只约束可见性
        # （for_agent 计算），invoke 不校验（编名直调是已知边界）。
        self._visible_to: Dict[str, List[str]] = {}
        # 提供者名 → 可见名单来源（静态 dict 或 ``(specs) -> dict`` 策略
        # callable）。名单是注册期的快照，工具集刷新（refresh_provider_tools）
        # 时需要按来源对**新**工具集重新派生名单——尤其 fail-closed 名单：
        # 新出现的工具若套用旧快照 + "未列出 = 全员"默认，会对所有 Agent
        # 泄露可见性，因此策略必须可重跑而非存快照。
        self._visible_to_source: Dict[str, Any] = {}
        # 生命周期批量启停的一次性旗标（start_providers/stop_providers 幂等守卫）
        self._providers_started: bool = False

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
        # 停用声明转正：apply_disabled 先于本工具注册（连接型 Provider 降级
        # 登记后补注册）时，在此承接停用态，配置声明不因注册时序而失灵
        if registered_name in self._disabled_pending:
            self._disabled_pending.discard(registered_name)
            self._disabled.add(registered_name)
            logger.info(f"工具 '{registered_name}' 注册时承接既有的停用声明")
        logger.debug(f"工具 '{registered_name}' 已注册（provider={spec.provider}, kind={spec.kind}）")
        return True

    def register_provider(
        self,
        provider: ToolProvider,
        *,
        visible_to: Optional[VisibleToSource] = None,
    ) -> int:
        """注册一个 Provider 的全部工具。返回新注册数（去重不计）。

        命名模型：注册键 = 每个 spec 的派生全名（``<provider>_<工具名>``，
        唯一实现见 ``ToolSpec.full_name``）；spec **原样存储**（声明名保持
        裸名，不做任何改名拷贝）。provider 自声明的 ``category`` 一并记录
        （按 spec.provider 提供者名归组，供 ``list_categories()`` /
        ``list_tools(category=)`` 查询）。0 工具的 Provider 也照常登记
        （降级登记：连接失败的 MCP provider 先占位，恢复后经
        ``refresh_provider_tools`` 补注册）。

        提供者单名校验（fail-fast）：Provider 的 ``name`` 必须与其全部
        spec 的 ``provider`` 同值（同值同源）——不一致直接抛 ``ValueError``。
        这是"一个提供者一个短名"的注册期保证；探活/关闭/归属仍按对象
        引用（``_tool_owner``）工作，不依赖字符串。

        可见名单（``visible_to``，生产侧声明）：静态 dict 键 = 本次注册项
        声明的**工具全名**，值 = 可见的 Agent 注册名列表或 ``["*"]``（全员）；
        或传 ``(specs) -> dict`` **策略 callable**——注册期先对当前工具集
        求值出静态名单，来源保存供 ``refresh_provider_tools`` 对新工具集
        重新派生。校验 fail-fast：值非空且元素为非空字符串、``"*"`` 只能
        单独出现、键必须命中本注册项声明的工具全名（拼错即报错）。
        **未列出的工具默认 ``DEFAULT_VISIBLE_TO``**（仅主播；共享工具由
        注册处显式声明 ``["*"]``）。名单只
        约束可见性（``list_tools(for_agent=...)`` 按它计算工具列表）；
        ``invoke()`` 不校验——LLM 幻觉编名直调保留工具是已知的受众治理边界。

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
        visible_map = visible_to(specs) if callable(visible_to) else visible_to
        validated_lists = self._validate_visible_to(visible_map, declared_full_names)
        if not isinstance(provider, BaseToolProvider):
            logger.warning(
                f"Provider '{provider.name}'（class={type(provider).__name__}）"
                "非 BaseToolProvider 子类，属迁移遗留，无法参与探活约定；"
                "请继承 BaseToolProvider 并按需覆写 health_check"
            )
        self._providers.append(provider)
        # 名单来源原样保存（dict / callable）：工具集刷新时按来源对新工具集重派名单
        self._visible_to_source[provider.name] = visible_to
        category = getattr(provider, "category", "") or ""
        # 分类在登记期就落账（0 工具降级登记也要可见于分类目录）
        self._record_category(provider.name, category)
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

    def unregister_provider(self, provider: ToolProvider) -> int:
        """移除一个 Provider 及其名下全部工具；幂等（未注册 → 返回 0 不报错）。

        一致清理四类映射：``_providers``（引用）、``_tools``（工具实现）、
        ``_tool_owner``（归属）、``_visible_to``（可见名单），并连带清掉
        名下工具的熔断状态与该提供者的分类记录——移除后工具对
        list/invoke/探活/可见性计算整体消失，不留悬挂条目。

        工具归属按 spec.provider 提供者名匹配（与注册期"一个提供者一个
        短名"校验同源，不同 provider 不会撞名）；同时按 ``_tool_owner``
        对象引用兜底，覆盖非 ``BaseToolProvider`` 子类注册（无归属记录）
        的情形。

        Returns:
            实际移除的工具数（provider 本身未注册 → 0）。
        """
        if provider not in self._providers:
            logger.debug(f"Provider '{provider.name}' 未注册，unregister 跳过（幂等）")
            return 0
        self._providers.remove(provider)
        owned = [
            name
            for name, (spec, _) in self._tools.items()
            if spec.provider == provider.name or self._tool_owner.get(name) is provider
        ]
        for name in owned:
            self._tools.pop(name, None)
            self._tool_owner.pop(name, None)
            self._visible_to.pop(name, None)
            self._health.pop(name, None)
        self._categories.pop(provider.name, None)
        self._visible_to_source.pop(provider.name, None)
        logger.info(f"Provider '{provider.name}' 已移除（摘除 {len(owned)} 个工具，总数={len(self._tools)}）")
        return len(owned)

    async def start_providers(self) -> Dict[str, bool]:
        """批量启动 Provider 生命周期（组合根在全部装配完成后调用一次）。

        对每个注册的 ``BaseToolProvider`` 调用 ``setup()``：维护外部连接的
        Provider 在其中建立连接、启动后台循环（如 VTS 建连 + 断线重连 +
        idle 动画）；无状态 Provider 沿用基类默认（no-op）。
        ``manages_own_lifecycle=True`` 的 Provider（MCP：装配期自行 setup 并
        回调收尾）跳过，避免同一连接被建立两次。

        单 Provider 失败不阻断其余（逐个异常隔离，失败记 ERROR 并入报告；
        连接型 Provider 自身应带后台重连兜底，启动失败不等于永久不可用）。
        幂等：进程内一次性——重复调用返回空报告（Provider 的 setup 亦各有
        自身短路守卫）。

        Returns:
            ``{provider名: 是否成功}``，只含本次实际调用的 Provider；
            已启动过（幂等短路）返回 ``{}``。
        """
        if self._providers_started:
            return {}
        self._providers_started = True
        report: Dict[str, bool] = {}
        for provider in list(self._providers):
            if not isinstance(provider, BaseToolProvider) or provider.manages_own_lifecycle:
                continue
            try:
                await provider.setup()
                report[provider.name] = True
            except Exception as exc:  # noqa: BLE001 — 单 Provider 隔离边界
                report[provider.name] = False
                logger.error(
                    f"Provider '{provider.name}' setup 失败（不阻断其余，交由其重连机制兜底）: "
                    f"{type(exc).__name__}: {exc}"
                )
        if report:
            ok_names = [name for name, ok in report.items() if ok]
            fail_names = [name for name, ok in report.items() if not ok]
            summary = f"Provider 生命周期启动完成: 成功 {len(ok_names)}/{len(report)}"
            if fail_names:
                summary += f"，失败: {fail_names}"
            logger.info(summary)
        return report

    async def stop_providers(self) -> None:
        """批量收尾 Provider 生命周期（停机路径调用，与 ``start_providers`` 对称）。

        对同一集合（``BaseToolProvider`` 且非自管理）逐个 ``cleanup()``，
        异常隔离不阻断其余；未启动过时跳过（装配失败中途退出的场景，
        Provider 自身无资源可释放）。MCP 的子进程关闭仍由
        ``close_mcp_providers`` 专责处理，不在此处。
        """
        if not self._providers_started:
            return
        self._providers_started = False
        for provider in list(self._providers):
            if not isinstance(provider, BaseToolProvider) or provider.manages_own_lifecycle:
                continue
            try:
                await provider.cleanup()
            except Exception as exc:  # noqa: BLE001 — 单 Provider 隔离边界
                logger.error(f"Provider '{provider.name}' cleanup 失败（继续其余）: {type(exc).__name__}: {exc}")

    def refresh_provider_tools(self, provider: ToolProvider) -> Dict[str, Any]:
        """重新登记已注册 Provider 的工具集（连接恢复 / 工具清单变化后的补注册）。

        与 ``register_provider`` 的分工：register 是首次装配（新增 Provider），
        refresh 是**常驻登记前提下的工具集换血**——摘除该 Provider 名下旧条目
        （工具 / 归属 / 可见名单；消失的工具连带清除熔断状态，存续工具保留
        "熔断待探活复位"），按 provider 当前 ``list_tools()`` 结果重新登记。
        Provider 对象与其分类记录保留（``_providers`` 不动）。
        可见名单按注册时保存的来源重新派生：策略 callable 对新工具集重跑
        （fail-closed 名单的正确形态）；静态 dict 过滤掉已消失的工具键后沿用
        （键全靠手工维护的共享名单，新工具落"未列出 = 全员"默认）。

        前提：provider 自身的 specs 缓存已先行刷新（如 ``McpToolProvider``.
        ``reconnect`` 重拉工具清单后）才调用本方法。

        Returns:
            ``{"ok": True, "provider_id", "added": [新登记全名],
            "removed": [已消失全名], "count": 该 Provider 当前工具总数}``；
            未注册 → ``{"ok": False, "error": ...}``。名单派生非法
            （校验不过）抛 ``ValueError``，由调用方兜底。
        """
        if provider not in self._providers:
            logger.warning(f"工具集刷新失败：未注册 Provider '{provider.name}'")
            return {"ok": False, "error": f"未注册 Provider: {provider.name}"}
        specs = list(provider.list_tools())
        mismatched = [s for s in specs if s.provider != provider.name]
        if mismatched:
            detail = ", ".join(f"spec({s.provider!r}, {s.name!r})" for s in mismatched)
            return {
                "ok": False,
                "error": f"Provider 名与其 spec.provider 必须同值同源: [{detail}]",
            }
        declared_full_names = {s.full_name for s in specs}
        old_names = self._tools_owned_by(provider)
        new_names = declared_full_names
        for name in old_names:
            self._tools.pop(name, None)
            self._tool_owner.pop(name, None)
            self._visible_to.pop(name, None)
            if name not in new_names:
                # 熔断历史只随工具消失而清除；存续工具保留"待探活复位"状态，
                # 不能借刷新把"熔断待核实"洗成"健康"（reconnect 流程随后探活）
                self._health.pop(name, None)
        # 分类落账保鲜（0 工具换血也不丢分类归属）
        category = getattr(provider, "category", "") or ""
        self._record_category(provider.name, category)
        source = self._visible_to_source.get(provider.name)
        if callable(source):
            validated_lists = self._validate_visible_to(source(specs), declared_full_names)
        elif isinstance(source, dict):
            stale = [k for k in source if k not in declared_full_names]
            if stale:
                logger.warning(f"Provider '{provider.name}' 静态可见名单含已消失的工具键（已忽略）: {sorted(stale)}")
            validated_lists = self._validate_visible_to(
                {k: v for k, v in source.items() if k in declared_full_names},
                declared_full_names,
            )
        else:
            validated_lists = None
        # added 语义 = 新旧集合差（不是注册器去重计数——换血路径旧条目已摘除，
        # 存续工具也会重新走一遍 register）
        added = [n for n in new_names if n not in set(old_names)]
        for spec in specs:
            registered_name = spec.full_name
            self.register(spec, provider.invoke)
            if isinstance(provider, BaseToolProvider):
                self._tool_owner[registered_name] = provider
            if validated_lists is not None:
                self._visible_to[registered_name] = validated_lists[registered_name]
        removed = [n for n in old_names if n not in declared_full_names]
        logger.info(
            f"Provider '{provider.name}' 工具集已刷新"
            f"（旧 {len(old_names)} → 新 {len(specs)}，新增 {len(added)}，移除 {len(removed)}）"
        )
        return {
            "ok": True,
            "provider_id": provider.name,
            "added": added,
            "removed": removed,
            "count": len(specs),
        }

    def list_providers(self) -> List[Dict[str, Any]]:
        """列出全部已登记 Provider 的运营摘要（工具页提供者卡片数据源）。

        每项：``name`` / ``category``（自声明，未声明空串）/ ``tool_count``
        （当前登记工具数，含停用与熔断）/ ``disabled_count`` /
        ``supports_reconnect`` / ``last_error``（Provider 侧最近一次连接失败
        摘要，无则空串）/ ``switch``（提供者开关地址声明，如 Agent 私有
        MCP 的 ``{"file", "key"}``，未声明为 None）。**0 工具的 Provider
        也在列**——降级登记（连接失败）的 Provider 靠这条在工具页保持
        可见、可手动重连。
        """
        result: List[Dict[str, Any]] = []
        for p in self._providers:
            owned = [name for name, owner in self._tool_owner.items() if owner is p]
            if not owned:
                # 非 BaseToolProvider 注册无归属记录：按 spec.provider 名兜底
                owned = [name for name, (spec, _) in self._tools.items() if spec.provider == p.name]
            switch = getattr(p, "switch_config", None)
            result.append(
                {
                    "name": p.name,
                    "category": self._categories.get(p.name, ""),
                    "tool_count": len(owned),
                    "disabled_count": sum(1 for n in owned if n in self._disabled),
                    "supports_reconnect": isinstance(p, BaseToolProvider) and bool(p.supports_reconnect),
                    "last_error": str(getattr(p, "last_error", "") or ""),
                    "switch": dict(switch) if isinstance(switch, dict) else None,
                }
            )
        return result

    @staticmethod
    def _validate_visible_to(
        visible_to: Optional[Dict[str, List[str]]],
        declared_full_names: set[str],
    ) -> Optional[Dict[str, List[str]]]:
        """校验可见名单并按全名补全（未列工具填默认 ``DEFAULT_VISIBLE_TO``）；非法即抛错。

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
            entries = visible_to.get(full_name, DEFAULT_VISIBLE_TO)
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
        """名单判定：未声明（不在表中）= 默认名单（仅主播）；声明则须命中该 Agent 名或 "*"。"""
        entries = self._visible_to.get(full_name)
        if entries is None:
            return agent_name in DEFAULT_VISIBLE_TO
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
        """返回工具的可见名单（未声明 = 默认名单，返回 ``DEFAULT_VISIBLE_TO`` 快照）。

        供运营面（Dashboard 等）标注"这个工具谁能看见"。名单只约束
        可见性，``invoke()`` 不校验（编名直调是已知边界）。
        """
        entries = self._visible_to.get(full_name)
        return list(entries) if entries is not None else list(DEFAULT_VISIBLE_TO)

    # -------------------- 停用 --------------------

    def apply_disabled(self, names: Iterable[str]) -> int:
        """整体设置停用集合（组合根装配完成后调用）。

        未匹配任何已注册工具的条目进入**待生效集**（``_disabled_pending``）：
        连接型 Provider 降级登记（连接失败先注册 0 工具、连接成功后补注册）
        时，指向其工具的停用条目在声明期尚未注册——进待生效集，待工具注册
        时自动转正为停用，配置声明不因注册时序而失灵。长期不注册仍可能是
        工具改名/拼错，记 warning 提示排查。

        Returns:
            已注册且立即生效的停用工具数（待生效集不计入）。
        """
        name_set = set(names)
        self._disabled = {n for n in name_set if n in self._tools}
        self._disabled_pending = name_set - self._disabled
        unmatched = sorted(self._disabled_pending)
        if unmatched:
            logger.warning(
                f"停用列表中有 {len(unmatched)} 个未注册工具（可能已改名或拼错；"
                f"连接型 Provider 降级登记时属预期，注册后自动生效）: {unmatched}"
            )
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
                exc=True,
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
                exc=True,
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

        # 重连成功先刷新工具集（MCP 降级登记 0 工具 → 重连后补注册；server
        # 侧清单变化 → 换血），再对归属工具做熔断探活复位
        try:
            refresh = self.refresh_provider_tools(provider)
        except Exception as exc:  # noqa: BLE001 - 刷新异常不推翻重连成果，报告携带原因
            logger.error(
                f"Provider '{provider_id}' 重连后工具集刷新异常: {type(exc).__name__}: {exc}",
                exc=True,
            )
            refresh = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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
            + (f"，工具集新增 {len(refresh.get('added', []))} 个" if refresh.get("ok") else "")
        )
        return {
            "ok": True,
            "provider_id": provider_id,
            "recovered": recovered,
            "still_tripped": still_tripped,
            "refreshed": {
                "added": refresh.get("added", []),
                "removed": refresh.get("removed", []),
                "count": refresh.get("count", 0),
            }
            if refresh.get("ok")
            else None,
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
        self._disabled_pending.clear()
        self._health.clear()
        self._tool_owner.clear()
        self._visible_to.clear()
        self._visible_to_source.clear()
        logger.debug("ToolRegistry 已清空")

    # 兼容 inspect / debug
    def __repr__(self) -> str:
        tripped = len(self.tripped_tools)
        return f"<ToolRegistry tools={len(self._tools)} providers={len(self._providers)} tripped={tripped}>"


__all__ = ["ToolRegistry", "default_tool_registry", "set_default_registry"]


# Pylance: 暴露 inspect 工具（避免 unused-import 警告）
_ = inspect
