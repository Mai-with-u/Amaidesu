"""组件运行时启停服务

采集器 / Agent 的动态启停与"按新配置重建"能力（实例化 → 注册 → 启动，
经 CollectorManager / AgentManager），供两条调用路径共享：

- 组件管理 API 的 START/STOP/RESTART 按钮；
- 配置保存管线对 agents / collectors 域变更的热应用（保存即生效）。

本模块不依赖 fastapi、不感知 HTTP 语义；基础设施引用（管理器、事件总线、
LLM/提示词管理器）由调用方经 :class:`ComponentRuntimeContext` 注入，
不导入 DashboardServer，避免服务层倒挂。

启用名单（enabled）的配置写回不在此模块——那是"装配意愿"的持久化，
属调用方职责（按钮路径同步写回，保存路径由写盘管线先行完成）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence, Set

import tomllib

from src.modules.agents.companions import start_companions, stop_companions
from src.modules.agents.manager import AgentDeps
from src.modules.collectors.factory import SUPPORTED_COLLECTORS
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.config.service import ConfigService

logger = get_logger("ComponentRuntime")

# 组件组名 → 是否 Agent 组（采集器组之外的组无动态启停语义，统一按未知处理）
_GROUP_AGENTS = "agents"
_GROUP_COLLECTORS = "collectors"


@dataclass
class ComponentRuntimeContext:
    """组件动态启停所需的基础设施引用（由组合层注入，均可为 None）。

    任一管理器缺失表示当前进程无该组组件的动态启停能力，操作按
    "不可动态执行"返回（按钮路径回退配置写回；保存路径如实回执）。
    """

    collector_manager: Optional[Any] = None
    agent_manager: Optional[Any] = None
    event_bus: Optional[Any] = None
    llm_manager: Optional[Any] = None
    prompt_manager: Optional[Any] = None

    @classmethod
    def from_server(cls, server: Any) -> "ComponentRuntimeContext":
        """从 DashboardServer 收集基础设施引用（缺失属性按 None 处理）。"""
        return cls(
            collector_manager=getattr(server, "collector_manager", None),
            agent_manager=getattr(server, "agent_manager", None),
            event_bus=getattr(server, "event_bus", None),
            llm_manager=getattr(server, "llm_manager", None),
            prompt_manager=getattr(server, "prompt_manager", None),
        )


@dataclass
class ComponentOpResult:
    """单组件一次动态操作的回执。

    - ``created``：本次是否新实例化（区别于启动已注册实例）；
    - ``skipped``：组件未在运行，无需重建（对保存热应用是正常结果）；
    - ``detail``：失败或跳过原因的中文描述。
    """

    success: bool
    created: bool = False
    skipped: bool = False
    detail: str = ""


def read_sub_config(config_service: Optional["ConfigService"], config_dir: Path, group: str, name: str) -> dict:
    """读取组件的子段配置 dict（无则空 dict）。

    - collectors → collectors.toml 顶层同名段（``[<name>]``），直读磁盘
      （写盘管线刚落盘的新值，合并视图未必需已刷新）；
    - agents → agents.toml ``[agents.<name>]``，读 ``main_config`` 合并视图
      （保存管线 reload 之后即新值）。
    """
    if group == _GROUP_COLLECTORS:
        path = Path(config_dir) / "collectors.toml"
        if not path.exists():
            return {}
        try:
            doc = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001 - 只读边界：坏文件按空段处理
            logger.warning(f"读取 collectors.toml 失败，按空文档处理: {exc}")
            return {}
        section = doc.get(name) if isinstance(doc, dict) else None
        return dict(section) if isinstance(section, dict) else {}
    if group == _GROUP_AGENTS:
        main_config = config_service.main_config if config_service else {}
        agents_section = main_config.get("agents") if isinstance(main_config, dict) else {}
        section = (agents_section or {}).get(name) if isinstance(agents_section, dict) else None
        return dict(section) if isinstance(section, dict) else {}
    return {}


async def start_instance(
    ctx: ComponentRuntimeContext, group: str, name: str, sub_config: dict
) -> Optional[ComponentOpResult]:
    """动态启动组件：已注册则启动现有实例，未注册则按配置实例化+注册+启动。

    返回 None 表示该组无管理器（无动态启停能力），由调用方决定回退。
    Agent 启动成功后附带确保附属采集器在运行（幂等，不重复启动）。
    """
    if group == _GROUP_COLLECTORS:
        manager = ctx.collector_manager
        if manager is None:
            return None
        if name not in SUPPORTED_COLLECTORS:
            return ComponentOpResult(
                success=False, detail=f"未知采集器名 '{name}'（合法名单：{list(SUPPORTED_COLLECTORS)}）"
            )
        if manager.get_collector_by_name(name) is not None:
            ok = await manager.start_collector(name)
            return ComponentOpResult(success=ok, detail="" if ok else "启动已注册实例失败")
        ok = await manager.enable_collector(name, sub_config, event_bus=ctx.event_bus)
        return ComponentOpResult(success=ok, created=ok, detail="" if ok else "动态启用失败")

    if group == _GROUP_AGENTS:
        manager = ctx.agent_manager
        if manager is None:
            return None
        if manager.get_agent_by_name(name) is not None:
            ok = await manager.start_agent(name)
            if ok:
                await _ensure_agent_companions(ctx, name, sub_config)
            return ComponentOpResult(success=ok, detail="" if ok else "启动已注册实例失败")
        ok = await manager.enable_agent(
            name,
            sub_config,
            deps=AgentDeps(
                llm_manager=ctx.llm_manager,
                prompt_manager=ctx.prompt_manager,
                event_bus=ctx.event_bus,
            ),
        )
        if ok:
            await _ensure_agent_companions(ctx, name, sub_config)
        return ComponentOpResult(success=ok, created=ok, detail="" if ok else "动态启用失败")

    return None


async def stop_instance(
    ctx: ComponentRuntimeContext, group: str, name: str, *, stop_agent_companions: bool = True
) -> Optional[bool]:
    """动态停止组件（stop + unregister）。

    返回三态：None = 无管理器或无已注册实例（无操作对象）；True/False = 已尝试。
    ``stop_agent_companions`` 控制是否连带停附属采集器——按钮停用路径连带停，
    按新配置重建路径不连带停（附属采集器不跟随 Agent 重建，避免漏事件）。
    """
    if group == _GROUP_COLLECTORS:
        manager = ctx.collector_manager
        if manager is None or manager.get_collector_by_name(name) is None:
            return None
        return await manager.disable_collector(name)

    if group == _GROUP_AGENTS:
        manager = ctx.agent_manager
        if manager is None or manager.get_agent_by_name(name) is None:
            return None
        ok = await manager.disable_agent(name)
        if ok and stop_agent_companions:
            await _stop_agent_companions(ctx, name)
        return ok

    return None


async def restart_component(ctx: ComponentRuntimeContext, group: str, name: str, sub_config: dict) -> ComponentOpResult:
    """按新配置重建组件：停 → 重新实例化 → 启动；进程不重启。

    未在运行的组件跳过（返回 ``skipped``，配置下次启动自然生效）；
    停止失败时保留旧实例继续运行并如实回执，不留下"停了但没起来"的空窗。
    """
    stopped = await stop_instance(ctx, group, name, stop_agent_companions=False)
    if stopped is None:
        return ComponentOpResult(success=True, skipped=True, detail="未在运行，跳过重建（下次启动生效）")
    if not stopped:
        return ComponentOpResult(success=False, detail="停止旧实例失败，旧实例保留继续运行")
    started = await start_instance(ctx, group, name, sub_config)
    if started is None:
        return ComponentOpResult(success=False, detail="停止成功但管理器不可用，无法重新启动")
    if not started.success:
        return ComponentOpResult(success=False, detail=f"已停止旧实例但按新配置启动失败：{started.detail}")
    return ComponentOpResult(success=True, created=True)


@dataclass
class HotApplyReport:
    """一次配置保存热应用的汇总回执（api 层翻译为响应消息）。"""

    #: 已按新配置重建并启动的组件名
    restarted: list[str] = field(default_factory=list)
    #: 未在运行而跳过重建的组件名（配置已写盘，下次启动生效）
    skipped: list[str] = field(default_factory=list)
    #: (组件名, 失败原因)：重建失败，运行中的旧配置实例可能已停止或保留
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def attempted(self) -> bool:
        """是否存在任何被变更键定位到的组件（区分"无组件可应用"与"应用过"）。"""
        return bool(self.restarted or self.skipped or self.failures)


def affected_components(keys: Sequence[str]) -> Dict[str, Set[str]]:
    """从变更键解析受影响组件：group（collectors/agents）→ 组件名集合。

    键形态：``collectors.<name>.<路径>`` / ``agents.agents.<name>.<路径>``。
    ``enabled`` / ``meta`` 键不定位具体组件（装配意愿整表或元数据），
    不触发组件重建。
    """
    affected: Dict[str, Set[str]] = {_GROUP_COLLECTORS: set(), _GROUP_AGENTS: set()}
    for key in keys:
        parts = key.split(".")
        if len(parts) < 2:
            continue
        if parts[0] == _GROUP_COLLECTORS and len(parts) >= 2 and parts[1] not in ("meta", "enabled"):
            affected[_GROUP_COLLECTORS].add(parts[1])
        elif (
            parts[0] == _GROUP_AGENTS
            and len(parts) >= 3
            and parts[1] == _GROUP_AGENTS
            and parts[2]
            not in (
                "meta",
                "enabled",
            )
        ):
            affected[_GROUP_AGENTS].add(parts[2])
    return affected


async def hot_apply_components(
    ctx: ComponentRuntimeContext,
    config_service: Optional["ConfigService"],
    config_dir: Path,
    keys: Sequence[str],
) -> HotApplyReport:
    """配置保存热应用入口：对变更键触碰的运行中组件执行按新配置重建。

    前置约定：新配置已写盘且 ``reload_config`` 已完成（本模块直读磁盘 /
    合并视图拿到的都是新值）。单组件失败不阻塞其余组件，逐条记入回执。
    """
    report = HotApplyReport()
    affected = affected_components(keys)
    for group in (_GROUP_COLLECTORS, _GROUP_AGENTS):
        for name in sorted(affected[group]):
            sub_config = read_sub_config(config_service, config_dir, group, name)
            result = await restart_component(ctx, group, name, sub_config)
            if result.skipped:
                report.skipped.append(name)
                logger.info(f"配置保存热应用：{group}/{name} 未在运行，跳过重建")
            elif result.success:
                report.restarted.append(name)
                logger.info(f"配置保存热应用：{group}/{name} 已按新配置重建并启动")
            else:
                report.failures.append((name, result.detail))
                logger.error(f"配置保存热应用：{group}/{name} 重建失败：{result.detail}")
    return report


async def _ensure_agent_companions(ctx: ComponentRuntimeContext, agent_name: str, agent_config: dict) -> None:
    """确保 Agent 的附属采集器在运行（已运行的不重复启动；无管理器时静默跳过）。"""
    manager = ctx.collector_manager
    if manager is None:
        return
    started = await start_companions(manager, agent_name, agent_config, ctx.event_bus)
    if started:
        logger.info(f"Agent {agent_name} 的附属采集器已随之启动：{started}")


async def _stop_agent_companions(ctx: ComponentRuntimeContext, agent_name: str) -> None:
    """停掉并移除 Agent 的附属采集器（无管理器时静默跳过）。"""
    manager = ctx.collector_manager
    if manager is None:
        return
    stopped = await stop_companions(manager, agent_name)
    if stopped:
        logger.info(f"Agent {agent_name} 的附属采集器已随之停止：{stopped}")
