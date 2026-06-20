"""
系统状态 API

提供系统运行状态的查询接口。
"""

import sys
import time
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.system import (
    HealthResponse,
    PhaseStatus,
    SystemStatsResponse,
    SystemStatusResponse,
)

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()

# 全局启动时间
_startup_time: float = time.time()


# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(server: ServerDep) -> SystemStatusResponse:
    """获取系统整体状态"""
    uptime = time.time() - _startup_time

    # 构建阶段状态
    input_phase = None
    decision_phase = None
    output_phase = None

    if server.input_manager:
        collectors = server.input_manager._collectors
        input_phase = PhaseStatus(
            enabled=True,
            active_components=len([c for c in collectors if getattr(c, "is_started", False)]),
            total_components=len(collectors),
        )

    if server.decision_manager:
        decider_names = list(server.decision_manager._deciders.keys())
        current = server.decision_manager._current_decider
        decision_phase = PhaseStatus(
            enabled=True,
            active_components=1 if current else 0,
            total_components=len(decider_names),
        )

    if server.output_manager:
        handler_names = server.output_manager.get_handler_names()
        output_phase = PhaseStatus(
            enabled=True,
            active_components=len(
                [
                    n
                    for n in handler_names
                    if server.output_manager.get_handler_by_name(n)
                    and getattr(server.output_manager.get_handler_by_name(n), "_is_started", False)
                ]
            ),
            total_components=len(handler_names),
        )

    return SystemStatusResponse(
        running=server._is_running,
        uptime_seconds=uptime,
        version="0.1.0",  # TODO: 从 config_service 获取
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        input_phase=input_phase,
        decision_phase=decision_phase,
        output_phase=output_phase,
    )


@router.get("/stats", response_model=SystemStatsResponse)
async def get_system_stats(server: ServerDep) -> SystemStatsResponse:
    """获取系统统计"""
    # TODO: 实现统计逻辑
    return SystemStatsResponse()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """健康检查"""
    return HealthResponse(status="ok", timestamp=time.time())
