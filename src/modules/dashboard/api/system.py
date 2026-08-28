"""
系统状态 API

提供系统运行状态的查询接口（v2 适配 Wave U1 / B1-B2）。

v2 变化：
- 移除 input/decision/output 阶段字段（旧版引用 v1 manager 但从未注入）
- 改为 groups: {collectors, agents, tools} + event_bus: {total_events} 视图
- 删除 /api/v1/system/stats 端点（与 SystemStatsResponse 一并废弃）
"""

import sys
import time
from typing import TYPE_CHECKING, Annotated, Any, Dict

from fastapi import APIRouter, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.system import (
    EventBusStats,
    GroupStatus,
    HealthResponse,
    SystemStatusResponse,
)
from src.modules.dashboard.utils.component_helper import get_v2_component_list

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()

# 全局启动时间
_startup_time: float = time.time()


# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(server: ServerDep) -> SystemStatusResponse:
    """获取系统整体状态（v2：基于采集器/Agent/工具三组 + EventBus 统计）。"""
    uptime = time.time() - _startup_time

    main_config = server.config_service.main_config if server.config_service else {}
    grouped = get_v2_component_list(main_config, server)

    groups: Dict[str, GroupStatus] = {
        "collectors": _build_group_status(grouped.get("collectors", [])),
        "agents": _build_group_status(grouped.get("agents", [])),
        "tools": _build_tool_status(server),
    }

    event_bus_stats = _build_event_bus_stats(server)

    return SystemStatusResponse(
        running=server._is_running,
        uptime_seconds=uptime,
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
        return EventBusStats(total_events=0)
    total = 0
    for entry in stats.values():
        emit_count = getattr(entry, "emit_count", 0)
        if isinstance(emit_count, int):
            total += emit_count
    return EventBusStats(total_events=total)


def _get_app_version() -> str:
    try:
        from importlib.metadata import version

        return version("amaidesu")
    except Exception:
        return "0.0.0"


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """健康检查"""
    return HealthResponse(status="ok", timestamp=time.time())
