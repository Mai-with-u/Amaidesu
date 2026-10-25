"""
系统状态 API

提供系统运行状态的查询接口。
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.system import (
    ChangelogResponse,
    DomainHealthItem,
    EventBusStats,
    GroupStatus,
    HealthResponse,
    SystemStatusResponse,
)
from src.modules.dashboard.utils.component_helper import build_config_view, get_v2_component_list
from src.modules.logging import get_logger
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
    )


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
    registry = getattr(server, "tool_registry", None)
    if registry is None:
        return GroupStatus(enabled=0, started=0, total=0)
    try:
        total = len(registry)
    except TypeError:
        total = 0
    # 工具无运行/启用语义，三者相等（前端按"available"理解）
    return GroupStatus(enabled=total, started=total, total=total)


def _build_event_bus_stats(server: "DashboardServer") -> EventBusStats:
    """聚合 EventBus 总吞吐量（emit_count 求和）。"""
    event_bus = getattr(server, "event_bus", None)
    if event_bus is None:
        return EventBusStats(total_events=0)
    getter = getattr(event_bus, "get_all_stats", None)
    if getter is None:
        return EventBusStats(total_events=0)
    try:
        stats = getter()
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
async def health_check(server: ServerDep) -> HealthResponse:
    """健康检查 + 连接健康墙聚合

    ``status``/``timestamp_ms`` 回答"进程活着"；``domains`` 只读探查各域
    既有管理器/注册表汇总连接态（TTS 引擎、工具提供者、采集器、Agent）。
    单域探查失败只降级为 unknown 项，不影响整体响应——健康墙是观测面，
    不能因观测本身故障而 500。
    """
    now = now_ms()
    return HealthResponse(
        status="ok",
        timestamp_ms=now,
        checked_at_ms=now,
        domains=_build_health_domains(server),
    )


def _build_health_domains(server: "DashboardServer") -> Dict[str, list[DomainHealthItem]]:
    """按域聚合连接态；每域独立兜底，探查异常降级为该域一个 unknown 项。"""
    builders = {
        "tts": _build_tts_items,
        "avatar": lambda s: _build_provider_items(s, {"avatar"}),
        "obs": lambda s: _build_provider_items(s, {"studio"}),
        "maibot": _build_maibot_items,
        "tools_providers": lambda s: _build_provider_items(s, None),
        "collectors": lambda s: _build_component_items(s, "collectors"),
        "agents": lambda s: _build_component_items(s, "agents"),
    }
    domains: Dict[str, Any] = {}
    for domain, build in builders.items():
        try:
            domains[domain] = build(server)
        except Exception:
            logger.warning(f"连接健康墙探查 {domain} 域失败，按 unknown 展示", exc=True)
            domains[domain] = [DomainHealthItem(name=domain, state="unknown", detail="状态探查失败")]
    return domains


def _build_tts_items(server: "DashboardServer") -> list[DomainHealthItem]:
    """TTS 域：透传引擎 ``get_stats()``（与 /debug/tts/status 同数据源）。"""
    engine = getattr(server, "tts_engine", None)
    if engine is None:
        return [
            DomainHealthItem(
                name="tts",
                state="unknown",
                detail="TTS 引擎未装配（未配置或极简启动）",
            )
        ]
    get_stats = getattr(engine, "get_stats", None)
    stats = dict(get_stats()) if callable(get_stats) else {}
    connected = stats.get("is_connected")
    if isinstance(connected, bool):
        state = "ok" if connected else "offline"
        detail = f"引擎 {stats.get('name', '')}".strip()
        return [DomainHealthItem(name="tts", state=state, detail=detail)]
    return [DomainHealthItem(name="tts", state="unknown", detail="引擎未暴露连接态")]


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

    连接态读取口径：公开属性优先（``is_connected`` / ``get_stats()``），
    ConnectionToolProvider 的私有 ``_connected`` 兜底——注册表未提供
    provider 对象的公开列举，此处只读访问 ``_providers`` 不做任何写入。
    """
    registry = getattr(server, "tool_registry", None)
    providers = list(getattr(registry, "_providers", None) or []) if registry is not None else []
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
    """只读探查 provider 连接态；各形态都查不到时返回 None（unknown）。"""
    value = getattr(provider, "is_connected", None)
    if isinstance(value, bool):
        return value
    getter = getattr(provider, "get_stats", None)
    if callable(getter):
        try:
            stats = getter()
        except Exception:
            logger.warning(f"读取 provider '{getattr(provider, 'name', '?')}' 状态失败", exc=True)
            stats = {}
        if isinstance(stats, dict) and isinstance(stats.get("is_connected"), bool):
            return bool(stats["is_connected"])
    raw = getattr(provider, "_connected", None)
    if isinstance(raw, bool):
        return raw
    return None


def _build_component_items(server: "DashboardServer", group: str) -> list[DomainHealthItem]:
    """采集器 / Agent 域：按组件清单逐项映射运行态。

    state 口径：已启动 → ok；已启用未启动 → degraded；配置未启用 → offline
    （停用是用户选择，但健康墙回答的是"现在跑没跑"，detail 说明原因）。
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
            state, detail = "offline", "配置未启用"
        items.append(DomainHealthItem(name=name, state=state, detail=detail))  # type: ignore[arg-type]
    return items


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
