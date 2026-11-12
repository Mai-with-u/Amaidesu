"""
系统状态 API

提供系统运行状态的查询接口。
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from src.modules.dashboard.dependencies import get_dashboard_server, server_component
from src.modules.dashboard.schemas.system import (
    ChangelogResponse,
    DomainHealthItem,
    EventBusStats,
    GroupStatus,
    HealthResponse,
    LLMFatalFaultItem,
    SystemStatusResponse,
)
from src.modules.dashboard.utils.component_helper import build_config_view, get_v2_component_list
from src.modules.logging import get_logger
from src.modules.tts.assembly import TTS_PROVIDER_NAMES
from src.modules.tts.protocol import TtsObservation
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("SystemAPI")

# 进程启动时刻（Unix 毫秒），运行时长由当前时刻回推；
# 日志/事件历史查询以它为默认窗口起点（"本次启动以来"）
_startup_time_ms: int = now_ms()


def startup_time_ms() -> int:
    """返回进程启动时刻（Unix 毫秒）。"""
    return _startup_time_ms


# CHANGELOG 位于仓库根（api → dashboard → modules → src → 仓库根）
_CHANGELOG_PATH = Path(__file__).resolve().parents[4] / "CHANGELOG.md"


# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]

# TTS 引擎观测接口契约（只读 get_stats）；健康墙观测缺席不报错，required=False
# 档按 None 透传，由 _build_tts_items 降级为 unknown 项
TtsObservationDep = Annotated[Optional[TtsObservation], Depends(server_component("tts_engine", required=False))]


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(server: ServerDep) -> SystemStatusResponse:
    """获取系统整体状态（v2：基于采集器/Agent/工具三组 + EventBus 统计）。"""
    uptime_ms = now_ms() - _startup_time_ms

    # 组件视图经公共构建器：collectors 来自 collectors.toml 直读（拍平主配置
    # 没有 "collectors" scope），agents 来自拍平主配置
    grouped = get_v2_component_list(build_config_view(server), server)

    groups: Dict[str, GroupStatus] = {
        "collectors": _build_group_status(grouped.get("collectors", [])),
        "agents": _build_group_status(grouped.get("agents", [])),
        "tools": _build_tool_status(server),
    }

    event_bus_stats = _build_event_bus_stats(server)

    return SystemStatusResponse(
        running=server._is_running,
        uptime_ms=uptime_ms,
        version=_get_app_version(),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        groups=groups,
        event_bus=event_bus_stats,
        llm_fatal_faults=_build_llm_fatal_faults(server),
    )


def _build_llm_fatal_faults(server: "DashboardServer") -> list[LLMFatalFaultItem]:
    """只读读取 LLMManager 当前尚未解除的用途档致命故障（余额/鉴权/连不上）。"""
    llm = server.llm_manager
    if llm is None or not hasattr(llm, "fatal_faults"):
        return []
    try:
        raw = llm.fatal_faults()
    except Exception:
        logger.warning("读取 LLM 致命故障快照失败，按无故障展示", exc=True)
        return []
    return [LLMFatalFaultItem(**item) for item in raw if isinstance(item, dict)]


def _build_group_status(summaries: list[Any]) -> GroupStatus:
    """从 ComponentSummary 列表构造 GroupStatus（采集器/Agent 共用）。

    enabled = is_enabled=True 的数量；started = is_started=True 的数量；
    total = 列表长度。
    """
    enabled = sum(1 for c in summaries if getattr(c, "is_enabled", False))
    started = sum(1 for c in summaries if getattr(c, "is_started", False))
    return GroupStatus(enabled=enabled, started=started, total=len(summaries))


def _build_tool_status(server: "DashboardServer") -> GroupStatus:
    """工具组状态：工具是被动能力契约，三项计数均等于注册表长度。"""
    registry = server.tool_registry
    if registry is None:
        return GroupStatus(enabled=0, started=0, total=0)
    total = len(registry)
    # 工具无运行/启用语义，三者相等（前端按"available"理解）
    return GroupStatus(enabled=total, started=total, total=total)


def _build_event_bus_stats(server: "DashboardServer") -> EventBusStats:
    """聚合 EventBus 总吞吐量（emit_count 求和）。"""
    event_bus = server.event_bus
    if event_bus is None:
        return EventBusStats(total_events=0)
    try:
        stats = event_bus.get_all_stats()
    except Exception:
        logger.warning("读取 EventBus 统计失败，按零吞吐展示", exc=True)
        return EventBusStats(total_events=0)
    total = 0
    for entry in stats.values():
        emit_count = getattr(entry, "emit_count", 0)
        if isinstance(emit_count, int):
            total += emit_count
    return EventBusStats(total_events=total)


def _get_app_version() -> str:
    try:
        # 函数内 import：importlib.metadata 导入即扫描已装发行版元数据，成本不低，仅在查询版本时付出
        from importlib.metadata import version

        return version("amaidesu")
    except Exception:
        logger.warning("读取 amaidesu 包版本失败，按未知版本展示", exc=True)
        return "0.0.0"


@router.get("/health", response_model=HealthResponse)
async def health_check(
    server: ServerDep,  # noqa: B008
    tts_observation: TtsObservationDep,  # noqa: B008
) -> HealthResponse:
    """健康检查 + 连接健康墙聚合

    ``status``/``timestamp_ms`` 回答"进程活着"；``domains`` 只读探查各域
    既有管理器/注册表汇总连接态（TTS 引擎、工具提供者、采集器、Agent）。
    单域探查失败只降级为 unknown 项，不影响整体响应——健康墙是只读观测，
    不能因观测本身故障而 500。
    """
    now = now_ms()
    return HealthResponse(
        status="ok",
        timestamp_ms=now,
        checked_at_ms=now,
        domains=_build_health_domains(server, tts_observation),
    )


def _build_health_domains(
    server: "DashboardServer",
    tts_observation: Optional[TtsObservation],
) -> Dict[str, list[DomainHealthItem]]:
    """按域聚合连接态；每域独立兜底，探查异常降级为该域一个 unknown 项。"""
    builders = {
        # TTS 域只依赖观测接口依赖（endpoint 注入），不读 server.tts_engine
        "tts": lambda s: _build_tts_items(s, tts_observation),
        "vision": _build_vision_items,
        "llm": _build_llm_items,
        "avatar": lambda s: _build_provider_items(s, {"avatar"}),
        "obs": lambda s: _build_provider_items(s, {"studio"}),
        "maibot": _build_maibot_items,
        "tools_providers": lambda s: _build_provider_items(s, None),
        "collectors": lambda s: _build_component_items(s, "collectors"),
        "agents": lambda s: _build_component_items(s, "agents", tts_observation),
    }
    domains: Dict[str, Any] = {}
    for domain, build in builders.items():
        try:
            domains[domain] = build(server)
        except Exception:
            logger.warning(f"连接健康墙探查 {domain} 域失败，按 unknown 展示", exc=True)
            domains[domain] = [DomainHealthItem(name=domain, state="unknown", detail="状态探查失败")]
    return domains


#: 健康墙状态严重度（Agent 依赖聚合取最差的口径；与前端 healthWall.ts 的
#: STATE_SEVERITY 同序）：ok < disabled < unknown < degraded < offline
_STATE_SEVERITY: Dict[str, int] = {"ok": 0, "disabled": 1, "unknown": 2, "degraded": 3, "offline": 4}


def _file_section(server: "DashboardServer", file_stem: str) -> Dict[str, Any]:
    """只读取一个配置文件的段集合；配置服务缺席或读取失败返回空 dict（调用方按缺席降级）。"""
    svc = server.config_service
    if svc is None:
        return {}
    try:
        section = svc.get_file_section(file_stem, {})
    except Exception:
        logger.warning(f"读取 {file_stem} 配置失败，健康墙按缺席降级", exc=True)
        return {}
    return section if isinstance(section, dict) else {}


def _build_tts_items(server: "DashboardServer", engine: Optional[TtsObservation]) -> list[DomainHealthItem]:
    """TTS 域：多引擎逐条列出，只标当前激活引擎的连接态。

    条目口径（装配表 ``TTS_PROVIDER_NAMES`` 为引擎名单唯一事实源）：
    总开关未开 / 未选为当前引擎 → disabled（配置上就没开，非故障）；
    激活引擎透传 ``get_stats()``（与 /debug/tts/status 同数据源），
    引擎未装配降级 unknown。
    """
    tts_cfg = _file_section(server, "infra").get("tts")
    tts_cfg = tts_cfg if isinstance(tts_cfg, dict) else {}
    enabled = bool(tts_cfg.get("enabled", True))
    active = str(tts_cfg.get("provider", "") or "")
    stats: Dict[str, Any] = dict(engine.get_stats()) if engine is not None else {}
    items: list[DomainHealthItem] = []
    for name in TTS_PROVIDER_NAMES:
        if not enabled:
            items.append(DomainHealthItem(name=name, state="disabled", detail="TTS 总开关未开"))
        elif name != active:
            items.append(DomainHealthItem(name=name, state="disabled", detail="未选为当前引擎"))
        elif engine is None:
            items.append(DomainHealthItem(name=name, state="unknown", detail="引擎未装配（未配置或极简启动）"))
        elif not isinstance(stats.get("is_connected"), bool):
            items.append(DomainHealthItem(name=name, state="unknown", detail="引擎未暴露连接态"))
        else:
            connected = bool(stats.get("is_connected"))
            state = "ok" if connected else "offline"
            detail = f"当前引擎 {stats.get('name', name)}".strip()
            items.append(DomainHealthItem(name=name, state=state, detail=detail))
    return items


def _build_vision_items(server: "DashboardServer") -> list[DomainHealthItem]:
    """视觉域两条目：画面采集（live_frame 采集器连接态）+ 视觉感知链路可用性。

    感知链路口径 = "决策请求挂最新直播画面"的三个前置：live_frame 采集器
    启用、主播配置开挂帧（agents.streamer.live_frame.enabled）、决策主模型
    支持视觉（model.toml 模型注册表 visual 标记）。任一前置缺失是配置上
    没开（disabled），不是故障；模型配置读不到按 unknown 降级。
    """
    grouped = get_v2_component_list(build_config_view(server), server)
    collectors = {str(c.name): c for c in grouped.get("collectors", [])}
    frame = collectors.get("live_frame")
    if frame is None:
        frame_state, frame_detail = "unknown", "live_frame 不在采集器清单"
    elif not bool(getattr(frame, "is_enabled", False)):
        frame_state, frame_detail = "disabled", "采集器未启用（collectors.toml）"
    elif not bool(getattr(frame, "is_started", False)):
        frame_state, frame_detail = "degraded", "已启用但未启动"
    else:
        frame_state, frame_detail = "ok", "运行中"
    items = [DomainHealthItem(name="live_frame", state=frame_state, detail=frame_detail)]  # type: ignore[arg-type]
    items.append(DomainHealthItem(name="vision_perception", state=_vision_perception_state(server, frame_state)))  # type: ignore[arg-type]
    return items


def _vision_perception_state(server: "DashboardServer", frame_state: str) -> str:
    """视觉感知条目状态：按"画面源 → 挂帧开关 → 主模型视觉能力"链路逐环判定。"""
    if frame_state in {"unknown", "disabled"}:
        return "disabled"
    main_config = server.config_service.main_config if server.config_service else {}
    agents_section = main_config.get("agents") if isinstance(main_config, dict) else {}
    streamer_cfg = agents_section.get("streamer") if isinstance(agents_section, dict) else {}
    live_frame_cfg = streamer_cfg.get("live_frame") if isinstance(streamer_cfg, dict) else {}
    if isinstance(live_frame_cfg, dict) and live_frame_cfg.get("enabled") is False:
        return "disabled"  # 画面有源但主播配置没开挂帧
    model_section = _file_section(server, "model")
    profiles = model_section.get("llm_profiles")
    if not isinstance(profiles, dict):
        return "unknown"  # 模型配置读不到，链路能力不可判
    planner_cfg = profiles.get("planner")
    planner_models = planner_cfg.get("model_list") if isinstance(planner_cfg, dict) else None
    if not isinstance(planner_models, list) or not planner_models:
        return "unknown"
    primary = _entries_by_name(model_section, "llm_models").get(str(planner_models[0]))
    if not isinstance(primary, dict):
        return "unknown"
    return "ok" if bool(primary.get("visual", False)) else "disabled"


def _entries_by_name(section: Dict[str, Any], key: str) -> Dict[str, Dict[str, Any]]:
    """把配置里的命名条目段收拢为 name → 条目（TOML 数组表与 dict 表两种形态都认）。"""
    raw = section.get(key)
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    if isinstance(raw, list):
        return {str(m.get("name", "")): m for m in raw if isinstance(m, dict)}
    return {}


def _build_llm_items(server: "DashboardServer") -> list[DomainHealthItem]:
    """LLM 服务域：各用途档（profile）的主/备模型配置解析状态。

    只读 model.toml 配置做解析（引用的模型是否注册、服务商密钥是否填写），
    不发真实 API 探测——余额耗尽/端点不可达类故障要探活须复用既有低频
    机制，健康墙的只读快照不制造额外请求。主模型未注册或其密钥未配置
    该档不可用；备模型缺位只降级（failover 少一条退路）。
    """
    model_section = _file_section(server, "model")
    profiles = model_section.get("llm_profiles")
    if not isinstance(profiles, dict) or not profiles:
        return [DomainHealthItem(name="llm", state="unknown", detail="模型配置不可读")]
    model_by_name = _entries_by_name(model_section, "llm_models")
    provider_by_name = _entries_by_name(model_section, "llm_providers")
    fatal_by_profile = {item.profile: item for item in _build_llm_fatal_faults(server)}
    items: list[DomainHealthItem] = []
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        model_list = profile.get("model_list")
        if not isinstance(model_list, list) or not model_list:
            items.append(DomainHealthItem(name=str(profile_name), state="unknown", detail="未配置模型名单"))
            continue
        primary, backups = str(model_list[0]), [str(m) for m in model_list[1:]]
        missing_backups = [m for m in backups if m not in model_by_name]
        primary_model = model_by_name.get(primary)
        active_fault = fatal_by_profile.get(str(profile_name))
        last_error = ""
        if active_fault is not None:
            state = "offline"
            detail = f"{active_fault.kind_label}（主 {primary}）"
            last_error = active_fault.detail
        elif primary_model is None:
            state, detail = "offline", f"主模型 {primary} 未注册"
        else:
            provider_cfg = provider_by_name.get(str(primary_model.get("api_provider", "")), {})
            if isinstance(provider_cfg, dict) and not str(provider_cfg.get("api_key", "") or "").strip():
                state, detail = "disabled", f"服务商 {primary_model.get('api_provider', '')} 密钥未配置"
            elif missing_backups:
                state, detail = "degraded", f"备模型未注册：{'、'.join(missing_backups)}"
            else:
                state, detail = "ok", f"主 {primary}" + (f" / 备 {'、'.join(backups)}" if backups else "")
        items.append(
            DomainHealthItem(name=str(profile_name), state=state, detail=detail, last_error=last_error)  # type: ignore[arg-type]
        )
    return items


def _build_maibot_items(server: "DashboardServer") -> list[DomainHealthItem]:
    """MaiBot 桥接域：近况上报器由进程根持有、未注入 DashboardServer，
    无可只读查询的连接态；按约定返回 unknown 占位，不硬造状态。"""
    return [
        DomainHealthItem(
            name="maibot_sync",
            state="unknown",
            detail="近况推送连接态未接入查询通道（见服务端日志）",
        )
    ]


def _build_provider_items(server: "DashboardServer", categories: set[str] | None) -> list[DomainHealthItem]:
    """工具提供者域连接态。``categories`` 为 None 时收其余全部提供者。

    连接态读取口径：provider 观测接口契约 ``connection_summary()``，Provider
    对象经注册表公开访问器 ``providers()`` 列举，不触碰私有成员。
    """
    registry = server.tool_registry
    providers = registry.providers() if registry is not None else []
    items: list[DomainHealthItem] = []
    for provider in providers:
        category = str(getattr(provider, "category", "") or "")
        if categories is not None:
            if category not in categories:
                continue
        elif category in {"avatar", "studio"}:
            continue
        items.append(_provider_health_item(provider))
    return items


def _provider_health_item(provider: Any) -> DomainHealthItem:
    """从单个 provider 对象只读推导健康墙条目。"""
    name = str(getattr(provider, "name", "") or type(provider).__name__)
    connected = _provider_connected(provider)
    last_error = str(getattr(provider, "last_error", "") or "")
    if connected is True:
        # 已连接但残留最近一次失败摘要：连接抖动过，按降级提示
        state = "degraded" if last_error else "ok"
    elif connected is False:
        state = "offline"
    else:
        state = "unknown"
    detail = {True: "已连接", False: "未连接", None: "连接态不可见"}[connected]
    return DomainHealthItem(
        name=name,
        state=state,  # type: ignore[arg-type]
        detail=detail,
        last_error=last_error,
        supports_reconnect=bool(getattr(provider, "supports_reconnect", False)),
    )


def _provider_connected(provider: Any) -> bool | None:
    """只读探查 provider 连接态：唯一口径是观测接口契约 ``connection_summary()``。

    返回三值：True / False / None（None = 平台未暴露连接态或契约读取失败，
    健康墙按 unknown 渲染）。迁移未完成的非契约 Provider（未继承
    ``BaseToolProvider``）同样按 unknown 降级，不再探测其私有成员。
    """
    try:
        summary = provider.connection_summary()
        connected = summary.get("is_connected") if isinstance(summary, dict) else None
    except Exception:
        logger.warning(f"读取 provider '{getattr(provider, 'name', '?')}' 连接态失败", exc=True)
        return None
    return connected if isinstance(connected, bool) else None


def _build_component_items(
    server: "DashboardServer",
    group: str,
    tts_observation: Optional[TtsObservation] = None,
) -> list[DomainHealthItem]:
    """采集器 / Agent 域：按组件清单逐项映射运行态。

    state 口径：已启动 → ok；已启用未启动 → degraded；配置未启用 →
    disabled（停用是用户选择，配置上就没开，非故障）。Agent 条目额外
    聚合其依赖的最差状态（``_agent_dependency_state``）。
    """
    grouped = get_v2_component_list(build_config_view(server), server)
    items: list[DomainHealthItem] = []
    for component in grouped.get(group, []):
        name = str(getattr(component, "name", "") or "?")
        enabled = bool(getattr(component, "is_enabled", False))
        started = bool(getattr(component, "is_started", False))
        if started:
            state, detail = "ok", "运行中"
        elif enabled:
            state, detail = "degraded", "已启用但未启动"
        else:
            state, detail = "disabled", "配置未启用"
        if group == "agents":
            state, detail = _apply_agent_dependencies(server, name, state, detail, tts_observation)
        items.append(DomainHealthItem(name=name, state=state, detail=detail))  # type: ignore[arg-type]
    return items


def _apply_agent_dependencies(
    server: "DashboardServer",
    name: str,
    state: str,
    detail: str,
    tts_observation: Optional[TtsObservation],
) -> tuple[str, str]:
    """把 Agent 依赖的最差状态聚合进条目。

    依赖映射来源：Agent 经 ``BaseAgent.register_tool_provider`` 登记进共享
    ToolRegistry 的提供者名单（游戏 Agent 的私有 MCP 提供者在此列，游戏
    通用——新游戏 Agent 登记后自动纳入，不写死具体游戏）；主播 Agent 另
    依赖 TTS 引擎（基础设施，不经注册表，按观测接口读连接态）。只看
    明确断开的依赖（offline）：连接不可见（unknown）的本地提供者不抬高
    条目严重度。Agent 自身配置未启用（disabled）时不被依赖状态覆盖。
    """
    if state == "disabled":
        return state, detail
    broken: list[str] = []
    manager = server.agent_manager
    agent = manager.get_agent_by_name(name) if manager is not None else None
    if agent is not None:
        for provider in agent.registered_tool_providers():
            if _provider_connected(provider) is False:
                broken.append(str(getattr(provider, "name", "") or "?"))
    if name == "streamer" and tts_observation is not None:
        stats = tts_observation.get_stats()
        if stats.get("is_connected") is False:
            broken.append("tts")
    if not broken:
        return state, detail
    return "offline", f"{detail}；依赖未连接：{'、'.join(broken)}"


@router.get("/changelog", response_model=ChangelogResponse)
async def get_changelog() -> ChangelogResponse:
    """读取 CHANGELOG.md 原文，供 WebUI 版本号弹窗渲染。

    每次请求现读文件（体量小），发布更新 CHANGELOG 后无需重启即可见。
    """
    try:
        content = _CHANGELOG_PATH.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=503, detail=f"CHANGELOG 文件不可读: {e}") from e
    return ChangelogResponse(content=content)
