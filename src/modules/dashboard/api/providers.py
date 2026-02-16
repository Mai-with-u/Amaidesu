"""
Provider 管理 API

提供 Provider 状态查询和控制接口。
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.provider import (
    ProviderControlAction,
    ProviderControlRequest,
    ProviderControlResponse,
    ProviderDetail,
    ProviderDetailResponse,
    ProviderListResponse,
)
from src.modules.dashboard.server import DashboardServer
from src.modules.dashboard.utils.provider_helper import (
    get_decision_provider_summaries,
    get_input_provider_summaries,
    get_output_provider_summaries,
    get_provider_detail,
)

router = APIRouter()


# 类型别名，用于依赖注入
ServerDep = Annotated[DashboardServer, Depends(get_dashboard_server)]


@router.get("", response_model=ProviderListResponse)
async def list_providers(server: ServerDep) -> ProviderListResponse:
    """获取所有 Provider 列表"""
    return ProviderListResponse(
        input=get_input_provider_summaries(server.input_manager),
        decision=get_decision_provider_summaries(server.decision_manager),
        output=get_output_provider_summaries(server.output_manager),
    )


@router.get("/{domain}/{name}", response_model=ProviderDetailResponse)
async def get_provider(
    domain: str,
    name: str,
    server: ServerDep,
) -> ProviderDetailResponse:
    """获取单个 Provider 详情"""
    if domain not in ["input", "decision", "output"]:
        raise HTTPException(status_code=400, detail=f"Invalid domain: {domain}")

    detail = get_provider_detail(
        domain,
        name,
        server.input_manager,
        server.decision_manager,
        server.output_manager,
    )

    if not detail:
        raise HTTPException(status_code=404, detail=f"Provider not found: {domain}/{name}")

    return ProviderDetailResponse(provider=ProviderDetail(**detail))


@router.post("/{domain}/{name}/control", response_model=ProviderControlResponse)
async def control_provider(
    domain: str,
    name: str,
    request: ProviderControlRequest,
    server: ServerDep,
) -> ProviderControlResponse:
    """控制 Provider（启动/停止/重启）"""
    if domain not in ["input", "decision", "output"]:
        raise HTTPException(status_code=400, detail=f"Invalid domain: {domain}")

    try:
        if domain == "input":
            return await _control_input_provider(name, request.action, server)
        elif domain == "decision":
            return await _control_decision_provider(name, request.action, server)
        elif domain == "output":
            return await _control_output_provider(name, request.action, server)
    except Exception as e:
        return ProviderControlResponse(success=False, message=str(e))

    return ProviderControlResponse(success=False, message=f"Unknown domain: {domain}")


async def _control_input_provider(
    name: str, action: ProviderControlAction, server: DashboardServer
) -> ProviderControlResponse:
    """控制 InputProvider"""
    manager = server.input_manager
    if not manager:
        return ProviderControlResponse(success=False, message="Input manager not available")

    # 查找 provider
    provider = None
    for p in getattr(manager, "_providers", []):
        info = p.get_info() if hasattr(p, "get_info") else {}
        provider_name = info.get("name", p.__class__.__name__)
        if provider_name == name:
            provider = p
            break

    if not provider:
        return ProviderControlResponse(success=False, message=f"Provider not found: {name}")

    if action == ProviderControlAction.STOP:
        if hasattr(provider, "stop"):
            await provider.stop()
        return ProviderControlResponse(success=True, message=f"Provider {name} stopped")
    elif action == ProviderControlAction.START:
        if hasattr(provider, "start"):
            await provider.start()
        return ProviderControlResponse(success=True, message=f"Provider {name} started")
    elif action == ProviderControlAction.RESTART:
        if hasattr(provider, "stop"):
            await provider.stop()
        if hasattr(provider, "start"):
            await provider.start()
        return ProviderControlResponse(success=True, message=f"Provider {name} restarted")

    return ProviderControlResponse(success=False, message=f"Unknown action: {action}")


async def _control_decision_provider(
    name: str, action: ProviderControlAction, server: DashboardServer
) -> ProviderControlResponse:
    """控制 DecisionProvider"""
    manager = server.decision_manager
    if not manager:
        return ProviderControlResponse(success=False, message="Decision manager not available")

    available = manager.get_available_providers() if hasattr(manager, "get_available_providers") else []
    if name not in available:
        return ProviderControlResponse(success=False, message=f"Provider not found: {name}")

    if action in [ProviderControlAction.START, ProviderControlAction.RESTART]:
        if hasattr(manager, "switch_provider"):
            # switch_provider 需要 config 参数
            await manager.switch_provider(name, {})
        return ProviderControlResponse(success=True, message=f"Switched to provider: {name}")
    elif action == ProviderControlAction.STOP:
        return ProviderControlResponse(success=False, message="Decision provider cannot be stopped")

    return ProviderControlResponse(success=False, message=f"Unknown action: {action}")


async def _control_output_provider(
    name: str, action: ProviderControlAction, server: DashboardServer
) -> ProviderControlResponse:
    """控制 OutputProvider"""
    manager = server.output_manager
    if not manager:
        return ProviderControlResponse(success=False, message="Output manager not available")

    provider = manager.get_provider_by_name(name) if hasattr(manager, "get_provider_by_name") else None
    if not provider:
        return ProviderControlResponse(success=False, message=f"Provider not found: {name}")

    if action == ProviderControlAction.STOP:
        if hasattr(provider, "stop"):
            await provider.stop()
        return ProviderControlResponse(success=True, message=f"Provider {name} stopped")
    elif action == ProviderControlAction.START:
        if hasattr(provider, "start"):
            await provider.start()
        return ProviderControlResponse(success=True, message=f"Provider {name} started")
    elif action == ProviderControlAction.RESTART:
        if hasattr(provider, "stop"):
            await provider.stop()
        if hasattr(provider, "start"):
            await provider.start()
        return ProviderControlResponse(success=True, message=f"Provider {name} restarted")

    return ProviderControlResponse(success=False, message=f"Unknown action: {action}")
