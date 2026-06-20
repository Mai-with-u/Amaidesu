"""
阶段参与者管理 API

提供 Collector/Decider/Handler 状态查询和控制接口。
"""

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.component import (
    ComponentControlAction,
    ComponentControlRequest,
    ComponentControlResponse,
    ComponentDetail,
    ComponentDetailResponse,
    ComponentListResponse,
)
from src.modules.dashboard.utils.component_helper import (
    get_collector_summaries,
    get_decider_summaries,
    get_handler_summaries,
    get_component_detail,
)

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()


# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("", response_model=ComponentListResponse)
async def list_components(server: ServerDep) -> ComponentListResponse:
    """获取所有组件列表"""
    return ComponentListResponse(
        input=get_collector_summaries(server.input_manager),
        decision=get_decider_summaries(server.decision_manager),
        output=get_handler_summaries(server.output_manager),
    )


@router.get("/{phase}/{name}", response_model=ComponentDetailResponse)
async def get_component(
    phase: str,
    name: str,
    server: ServerDep,
) -> ComponentDetailResponse:
    """获取单个组件详情"""
    if phase not in ["input", "decision", "output"]:
        raise HTTPException(status_code=400, detail=f"Invalid phase: {phase}")

    detail = get_component_detail(
        phase,
        name,
        server.input_manager,
        server.decision_manager,
        server.output_manager,
    )

    if not detail:
        raise HTTPException(status_code=404, detail=f"Component not found: {phase}/{name}")

    return ComponentDetailResponse(component=ComponentDetail(**detail))


@router.post("/{phase}/{name}/control", response_model=ComponentControlResponse)
async def control_component(
    phase: str,
    name: str,
    request: ComponentControlRequest,
    server: ServerDep,
) -> ComponentControlResponse:
    """控制组件（启动/停止/重启）"""
    if phase not in ["input", "decision", "output"]:
        raise HTTPException(status_code=400, detail=f"Invalid phase: {phase}")

    try:
        if phase == "input":
            return await _control_input_component(name, request.action, server)
        elif phase == "decision":
            return await _control_decision_component(name, request.action, server)
        elif phase == "output":
            return await _control_output_component(name, request.action, server)
    except Exception as e:
        return ComponentControlResponse(success=False, message=str(e))

    return ComponentControlResponse(success=False, message=f"Unknown stage: {phase}")


async def _control_input_component(
    name: str, action: ComponentControlAction, server: "DashboardServer"
) -> ComponentControlResponse:
    """控制 InputCollector"""
    manager = server.input_manager
    if not manager:
        return ComponentControlResponse(success=False, message="Input manager not available")

    component = None
    for c in manager._collectors:
        info = c.get_info()
        if info.get("name", c.__class__.__name__) == name:
            component = c
            break

    if not component:
        return ComponentControlResponse(success=False, message=f"Component not found: {name}")

    if action == ComponentControlAction.STOP:
        if hasattr(component, "stop"):
            await component.stop()
        return ComponentControlResponse(success=True, message=f"Component {name} stopped")
    elif action == ComponentControlAction.START:
        if hasattr(component, "start"):
            await component.start()
        return ComponentControlResponse(success=True, message=f"Component {name} started")
    elif action == ComponentControlAction.RESTART:
        if hasattr(component, "stop"):
            await component.stop()
        if hasattr(component, "start"):
            await component.start()
        return ComponentControlResponse(success=True, message=f"Component {name} restarted")

    return ComponentControlResponse(success=False, message=f"Unknown action: {action}")


async def _control_decision_component(
    name: str, action: ComponentControlAction, server: "DashboardServer"
) -> ComponentControlResponse:
    """控制 Decider"""
    manager = server.decision_manager
    if not manager:
        return ComponentControlResponse(success=False, message="Decision manager not available")

    available = manager.get_available_deciders() if hasattr(manager, "get_available_deciders") else []
    if name not in available:
        return ComponentControlResponse(success=False, message=f"Component not found: {name}")

    if action in [ComponentControlAction.START, ComponentControlAction.RESTART]:
        if hasattr(manager, "switch_decider"):
            await manager.switch_decider(name, {})
        return ComponentControlResponse(success=True, message=f"Switched to decider: {name}")
    elif action == ComponentControlAction.STOP:
        return ComponentControlResponse(success=False, message="Decision decider cannot be stopped")

    return ComponentControlResponse(success=False, message=f"Unknown action: {action}")


async def _control_output_component(
    name: str, action: ComponentControlAction, server: "DashboardServer"
) -> ComponentControlResponse:
    """控制 OutputHandler"""
    manager = server.output_manager
    if not manager:
        return ComponentControlResponse(success=False, message="Output manager not available")

    component = manager.get_handler_by_name(name) if hasattr(manager, "get_handler_by_name") else None
    if not component:
        return ComponentControlResponse(success=False, message=f"Component not found: {name}")

    if action == ComponentControlAction.STOP:
        if hasattr(component, "stop"):
            await component.stop()
        return ComponentControlResponse(success=True, message=f"Component {name} stopped")
    elif action == ComponentControlAction.START:
        if hasattr(component, "start"):
            await component.start()
        return ComponentControlResponse(success=True, message=f"Component {name} started")
    elif action == ComponentControlAction.RESTART:
        if hasattr(component, "stop"):
            await component.stop()
        if hasattr(component, "start"):
            await component.start()
        return ComponentControlResponse(success=True, message=f"Component {name} restarted")

    return ComponentControlResponse(success=False, message=f"Unknown action: {action}")
