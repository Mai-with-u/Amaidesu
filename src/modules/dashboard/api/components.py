"""
阶段参与者管理 API

提供 Collector/Decider/Handler 状态查询和控制接口。
"""

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Depends

from src.modules.config.toml_utils import (
    load_toml_with_comments,
    write_toml_preserve,
)
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

# phase → 配置 section（对应 TOML 文件的 [collectors]/[deciders]/[handlers] 段）
_PHASE_TO_SECTION = {
    "input": "collectors",
    "decision": "deciders",
    "output": "handlers",
}


def _enable_via_config(server: "DashboardServer", phase: str, name: str) -> ComponentControlResponse:
    """把未启用组件写入对应阶段 TOML 的 enabled 列表（配置驱动，重启后生效）。

    组件管理页对未启用组件点"启动/激活"时调用；已启用组件的启停仍走实例控制。
    """
    section = _PHASE_TO_SECTION[phase]
    config_path = server.get_config_path(section)
    if not config_path:
        return ComponentControlResponse(success=False, message="Config file path not available")

    try:
        doc = load_toml_with_comments(str(config_path))
        if section not in doc:
            doc[section] = {}
        enabled = list(doc[section].get("enabled") or [])
        if name not in enabled:
            enabled.append(name)
            doc[section]["enabled"] = enabled

        success, message = write_toml_preserve(str(config_path), doc, create_backup=False)
        if not success:
            return ComponentControlResponse(success=False, message=f"写入配置失败: {message}")
        return ComponentControlResponse(
            success=True,
            message=f"组件 {name} 已加入启用列表（{section}.enabled），重启后生效",
        )
    except Exception as e:
        return ComponentControlResponse(success=False, message=f"启用失败: {e}")


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


def _disable_via_config(server: "DashboardServer", phase: str, name: str) -> ComponentControlResponse:
    """把组件名从对应阶段 TOML 的 enabled 列表移除（配置同步，幂等）。"""
    section = _PHASE_TO_SECTION[phase]
    config_path = server.get_config_path(section)
    if not config_path:
        return ComponentControlResponse(success=False, message="Config file path not available")

    try:
        doc = load_toml_with_comments(str(config_path))
        if section not in doc:
            return ComponentControlResponse(success=True, message=f"{name} 不在启用列表中")
        enabled = list(doc[section].get("enabled") or [])
        if name not in enabled:
            return ComponentControlResponse(success=True, message=f"{name} 不在启用列表中")
        enabled.remove(name)
        doc[section]["enabled"] = enabled

        success, message = write_toml_preserve(str(config_path), doc, create_backup=False)
        if not success:
            return ComponentControlResponse(success=False, message=f"写入配置失败: {message}")
        return ComponentControlResponse(success=True, message=f"组件 {name} 已从启用列表移除")
    except Exception as e:
        return ComponentControlResponse(success=False, message=f"停用失败: {e}")


async def _control_input_component(
    name: str, action: ComponentControlAction, server: "DashboardServer"
) -> ComponentControlResponse:
    """控制 InputCollector：动态启停 + 配置 enabled 同步。"""
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
        # 未加载：START 动态创建并启动 + 写入配置；其余动作报错
        if action == ComponentControlAction.START:
            ok = await manager.enable_collector(name, server.config_service)
            if not ok:
                return ComponentControlResponse(success=False, message=f"组件 {name} 启动失败")
            _enable_via_config(server, "input", name)
            return ComponentControlResponse(success=True, message=f"组件 {name} 已启动并加入启用配置")
        return ComponentControlResponse(success=False, message=f"Component not found: {name}")

    if action == ComponentControlAction.STOP:
        await manager.disable_collector(name)
        _disable_via_config(server, "input", name)
        return ComponentControlResponse(success=True, message=f"组件 {name} 已停止并从启用配置移除")
    elif action == ComponentControlAction.START:
        if hasattr(component, "start"):
            await component.start()
        return ComponentControlResponse(success=True, message=f"组件 {name} 已启动")
    elif action == ComponentControlAction.RESTART:
        if hasattr(component, "stop"):
            await component.stop()
        if hasattr(component, "start"):
            await component.start()
        return ComponentControlResponse(success=True, message=f"组件 {name} 已重启")

    return ComponentControlResponse(success=False, message=f"Unknown action: {action}")


async def _control_decision_component(
    name: str, action: ComponentControlAction, server: "DashboardServer"
) -> ComponentControlResponse:
    """控制 Decider：动态启停单个（多 Decider 并行）+ 配置 enabled 同步。"""
    manager = server.decision_manager
    if not manager:
        return ComponentControlResponse(success=False, message="Decision manager not available")

    available = manager.get_available_deciders() if hasattr(manager, "get_available_deciders") else []
    if name not in available:
        return ComponentControlResponse(success=False, message=f"Component not found: {name}")

    decision_config = {}
    if server.config_service and server.config_service.main_config:
        decision_config = server.config_service.main_config.get("deciders") or {}

    if action == ComponentControlAction.START:
        ok = await manager.enable_decider(name, decision_config)
        if not ok:
            return ComponentControlResponse(success=False, message=f"Decider {name} 启动失败")
        _enable_via_config(server, "decision", name)
        return ComponentControlResponse(success=True, message=f"Decider {name} 已启动并加入启用配置")
    elif action == ComponentControlAction.STOP:
        await manager.disable_decider(name)
        _disable_via_config(server, "decision", name)
        return ComponentControlResponse(success=True, message=f"Decider {name} 已停止并从启用配置移除")
    elif action == ComponentControlAction.RESTART:
        await manager.disable_decider(name)
        ok = await manager.enable_decider(name, decision_config)
        if not ok:
            return ComponentControlResponse(success=False, message=f"Decider {name} 重启失败")
        return ComponentControlResponse(success=True, message=f"Decider {name} 已重启")

    return ComponentControlResponse(success=False, message=f"Unknown action: {action}")


async def _control_output_component(
    name: str, action: ComponentControlAction, server: "DashboardServer"
) -> ComponentControlResponse:
    """控制 OutputHandler：动态启停 + 配置 enabled 同步。"""
    manager = server.output_manager
    if not manager:
        return ComponentControlResponse(success=False, message="Output manager not available")

    component = manager.get_handler_by_name(name) if hasattr(manager, "get_handler_by_name") else None
    if not component:
        # 未加载：START 动态创建并启动 + 写入配置；其余动作报错
        if action == ComponentControlAction.START:
            ok = await manager.enable_handler(name, server.config_service)
            if not ok:
                return ComponentControlResponse(success=False, message=f"组件 {name} 启动失败")
            _enable_via_config(server, "output", name)
            return ComponentControlResponse(success=True, message=f"组件 {name} 已启动并加入启用配置")
        return ComponentControlResponse(success=False, message=f"Component not found: {name}")

    if action == ComponentControlAction.STOP:
        await manager.disable_handler(name)
        _disable_via_config(server, "output", name)
        return ComponentControlResponse(success=True, message=f"组件 {name} 已停止并从启用配置移除")
    elif action == ComponentControlAction.START:
        if hasattr(component, "start"):
            await component.start()
        return ComponentControlResponse(success=True, message=f"组件 {name} 已启动")
    elif action == ComponentControlAction.RESTART:
        if hasattr(component, "stop"):
            await component.stop()
        if hasattr(component, "start"):
            await component.start()
        return ComponentControlResponse(success=True, message=f"组件 {name} 已重启")

    return ComponentControlResponse(success=False, message=f"Unknown action: {action}")
