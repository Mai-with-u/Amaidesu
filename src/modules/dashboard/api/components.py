"""
组件管理 API（v2）

提供 采集器 / Agent / 工具 三组组件的状态查询和控制接口。
数据源为拍平后的主配置（ConfigService.main_config）与运行时
CollectorManager / AgentManager / ToolRegistry。
"""

from typing import TYPE_CHECKING, Annotated, Any, Dict, List

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
from src.modules.dashboard.utils.component_helper import get_v2_component_list

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]

# v2：phase（前端旧字段）→ (配置文件顶层段, doc 嵌套键路径, 显示组)
# doc 路径从 TOML 文件顶层段开始（tools.toml 顶层是 [tools]）
_PHASE_TO_V2_CONFIG: Dict[str, tuple[str, List[str], str]] = {
    "input": ("tools", ["tools", "perception", "config"], "collectors"),
    "decision": ("agents", ["agents"], "agents"),
    "output": ("tools", ["tools", "output", "config"], "tools"),
}


def _set_enabled_in_doc(doc: Dict[str, Any], path_keys: List[str], enabled: List[str]) -> None:
    """按嵌套键路径定位 doc 中的 enabled 列表并写回（自动创建缺失段）。"""
    node: Dict[str, Any] = doc
    for key in path_keys:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node["enabled"] = enabled


def _get_enabled_in_doc(doc: Dict[str, Any], path_keys: List[str]) -> List[str]:
    node: Dict[str, Any] = doc
    for key in path_keys:
        child = node.get(key)
        if not isinstance(child, dict):
            return []
        node = child
    enabled = node.get("enabled")
    return list(enabled) if isinstance(enabled, list) else []


def _sync_enabled_config(server: "DashboardServer", phase: str, name: str, *, enable: bool) -> ComponentControlResponse:
    """把组件名加入/移除对应配置段的 enabled 列表（幂等写回）。

    phase 映射（v2）：
    - input（采集器）→ tools.toml ``[tools.perception.config].enabled``
    - decision（Agent）→ agents.toml ``[agents].enabled``
    - output（工具）→ tools.toml ``[tools.output.config].enabled``
    """
    section_info = _PHASE_TO_V2_CONFIG.get(phase)
    if section_info is None:
        return ComponentControlResponse(success=False, message=f"未知阶段: {phase}")
    top_section, path_keys, _ = section_info

    config_path = server.get_config_path(top_section)
    if not config_path:
        return ComponentControlResponse(success=False, message="Config file path not available")

    try:
        doc = load_toml_with_comments(str(config_path))
        enabled = _get_enabled_in_doc(doc, path_keys)
        if enable and name not in enabled:
            enabled.append(name)
        elif not enable and name in enabled:
            enabled.remove(name)
        _set_enabled_in_doc(doc, path_keys, enabled)

        success, message = write_toml_preserve(str(config_path), doc, create_backup=False)
        if not success:
            return ComponentControlResponse(success=False, message=f"写入配置失败: {message}")
        action_text = "加入启用列表" if enable else "从启用列表移除"
        return ComponentControlResponse(
            success=True,
            message=f"组件 {name} 已{action_text}（{config_path}），重启后生效",
        )
    except Exception as e:
        return ComponentControlResponse(success=False, message=f"配置同步失败: {e}")


@router.get("", response_model=ComponentListResponse)
async def list_components(server: ServerDep) -> ComponentListResponse:
    """获取所有组件列表（v2：采集器 / Agent / 工具，含未启用组件）"""
    main_config = server.config_service.main_config if server.config_service else {}
    grouped = get_v2_component_list(main_config, server)
    return ComponentListResponse(
        collectors=grouped["collectors"],
        agents=grouped["agents"],
        tools=grouped["tools"],
    )


@router.get("/{phase}/{name}", response_model=ComponentDetailResponse)
async def get_component(
    phase: str,
    name: str,
    server: ServerDep,
) -> ComponentDetailResponse:
    """获取单个组件详情"""
    main_config = server.config_service.main_config if server.config_service else {}
    grouped = get_v2_component_list(main_config, server)
    group = _PHASE_TO_V2_CONFIG.get(phase, (None, None, phase))[2]
    target_group = "collectors" if group == "collectors" else ("agents" if group == "agents" else "tools")
    for summary in grouped.get(target_group, []):
        if summary.name == name:
            return ComponentDetailResponse(component=ComponentDetail(**summary.model_dump()))

    raise HTTPException(status_code=404, detail=f"Component not found: {phase}/{name}")


@router.post("/{phase}/{name}/control", response_model=ComponentControlResponse)
async def control_component(
    phase: str,
    name: str,
    request: ComponentControlRequest,
    server: ServerDep,
) -> ComponentControlResponse:
    """控制组件：优先动态启停（实例→配置），失败回退配置写回（重启后生效）"""
    if phase not in _PHASE_TO_V2_CONFIG:
        raise HTTPException(status_code=400, detail=f"Invalid phase: {phase}")

    if request.action == ComponentControlAction.START:
        dyn = await _try_dynamic_start(server, phase, name)
        if dyn is not None and dyn.success:
            resp = dyn
        else:
            resp = _sync_enabled_config(server, phase, name, enable=True)
    elif request.action == ComponentControlAction.STOP:
        dyn_resp = await _try_dynamic_stop(server, phase, name)
        resp = dyn_resp or _sync_enabled_config(server, phase, name, enable=False)
    elif request.action == ComponentControlAction.RESTART:
        stop_resp = await _try_dynamic_stop(server, phase, name)
        stop_resp = stop_resp or _sync_enabled_config(server, phase, name, enable=False)
        if not stop_resp.success:
            return stop_resp
        dyn = await _try_dynamic_start(server, phase, name)
        resp = dyn if dyn is not None and dyn.success else _sync_enabled_config(server, phase, name, enable=True)
    else:
        return ComponentControlResponse(success=False, message=f"Unknown action: {request.action}")

    if resp.success and server.config_service is not None:
        await server.config_service.reload_config()
    return resp


async def _try_dynamic_start(server: "DashboardServer", phase: str, name: str) -> ComponentControlResponse | None:
    """尝试动态启动组件（实例化+注册+start）。返回 None 表示不可动态启动。"""
    top_section, _path_keys, group = _PHASE_TO_V2_CONFIG[phase]
    main_config = server.config_service.main_config if server.config_service else {}
    sub_cfg = _read_sub_config(main_config, top_section, phase, name)

    if group == "collectors":
        manager = server.collector_manager
        if manager is None:
            return None
        if manager.get_collector_by_name(name) is not None:
            ok = await manager.start_collector(name)
            return ComponentControlResponse(
                success=ok, message=f"组件 {name} 已启动" if ok else f"组件 {name} 启动失败"
            )
        ok = await manager.enable_collector(name, sub_cfg, event_bus=getattr(server, "event_bus", None))
        if ok:
            _sync_enabled_config(server, phase, name, enable=True)
            return ComponentControlResponse(success=True, message=f"组件 {name} 已启动并加入启用配置")
        return ComponentControlResponse(success=False, message=f"组件 {name} 启动失败（已尝试动态启用）")

    if group == "agents":
        manager = server.agent_manager
        if manager is None:
            return None
        if manager.get_agent_by_name(name) is not None:
            ok = await manager.start_agent(name)
            return ComponentControlResponse(
                success=ok, message=f"Agent {name} 已启动" if ok else f"Agent {name} 启动失败"
            )
        ok = await manager.enable_agent(
            name,
            sub_cfg,
            llm_manager=getattr(server, "llm_manager", None),
            prompt_manager=getattr(server, "prompt_manager", None),
            context_service=getattr(server, "context_service", None),
            event_bus=getattr(server, "event_bus", None),
        )
        if ok:
            _sync_enabled_config(server, phase, name, enable=True)
            return ComponentControlResponse(success=True, message=f"Agent {name} 已启动并加入启用配置")
        return ComponentControlResponse(success=False, message=f"Agent {name} 启动失败（已尝试动态启用）")

    return None  # tools 组：无实例语义，走配置写回


async def _try_dynamic_stop(server: "DashboardServer", phase: str, name: str) -> ComponentControlResponse | None:
    """尝试动态停止组件（stop+unregister+配置移除）。返回 None 表示无实例可停。"""
    top_section, _path_keys, group = _PHASE_TO_V2_CONFIG[phase]

    if group == "collectors":
        manager = server.collector_manager
        if manager is None or manager.get_collector_by_name(name) is None:
            return None
        ok = await manager.disable_collector(name)
        if ok:
            _sync_enabled_config(server, phase, name, enable=False)
            return ComponentControlResponse(success=True, message=f"组件 {name} 已停止并从启用配置移除")
        return ComponentControlResponse(success=False, message=f"组件 {name} 停止失败")

    if group == "agents":
        manager = server.agent_manager
        if manager is None or manager.get_agent_by_name(name) is None:
            return None
        ok = await manager.disable_agent(name)
        if ok:
            _sync_enabled_config(server, phase, name, enable=False)
            return ComponentControlResponse(success=True, message=f"Agent {name} 已停止并从启用配置移除")
        return ComponentControlResponse(success=False, message=f"Agent {name} 停止失败")

    return None


def _read_sub_config(main_config: dict, top_section: str, phase: str, name: str) -> dict:
    """读取组件的子段配置 dict（无则空 dict）。"""
    if top_section == "tools":
        if phase == "input":
            return dict((main_config.get("tools") or {}).get("perception", {}).get("config", {}).get(name) or {})
        if phase == "output":
            return dict((main_config.get("tools") or {}).get("output", {}).get("config", {}).get(name) or {})
    if top_section == "agents":
        return dict((main_config.get("agents") or {}).get(name) or {})
    return {}
