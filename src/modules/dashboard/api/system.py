"""
系统状态 API

提供系统运行状态的查询接口。
"""

import sys
import time
from typing import Annotated

from fastapi import APIRouter, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.system import (
    DomainStatus,
    HealthResponse,
    SystemStatsResponse,
    SystemStatusResponse,
)
from src.modules.dashboard.server import DashboardServer

router = APIRouter()

# 全局启动时间
_startup_time: float = time.time()


# 类型别名，用于依赖注入
ServerDep = Annotated[DashboardServer, Depends(get_dashboard_server)]


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(server: ServerDep) -> SystemStatusResponse:
    """获取系统整体状态"""
    uptime = time.time() - _startup_time

    # 构建 Domain 状态
    input_domain = None
    decision_domain = None
    output_domain = None

    if server.input_manager:
        providers = server.input_manager._providers if hasattr(server.input_manager, "_providers") else []
        input_domain = DomainStatus(
            enabled=True,
            active_providers=len([p for p in providers if getattr(p, "is_started", False)]),
            total_providers=len(providers),
        )

    if server.decision_manager:
        current = server.decision_manager.get_current_provider()
        available = (
            server.decision_manager.get_available_providers()
            if hasattr(server.decision_manager, "get_available_providers")
            else []
        )
        decision_domain = DomainStatus(
            enabled=True,
            active_providers=1 if current else 0,
            total_providers=len(available),
        )

    if server.output_manager:
        names = (
            server.output_manager.get_provider_names() if hasattr(server.output_manager, "get_provider_names") else []
        )
        output_domain = DomainStatus(
            enabled=True,
            active_providers=len(
                [
                    n
                    for n in names
                    if server.output_manager.get_provider_by_name(n)
                    and getattr(server.output_manager.get_provider_by_name(n), "is_started", False)
                ]
            ),
            total_providers=len(names),
        )

    return SystemStatusResponse(
        running=server._is_running,
        uptime_seconds=uptime,
        version="0.1.0",  # TODO: 从 config_service 获取
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        input_domain=input_domain,
        decision_domain=decision_domain,
        output_domain=output_domain,
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
