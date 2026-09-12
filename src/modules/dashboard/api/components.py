"""
组件管理 API

提供 采集器 / Agent 两组组件的状态查询和控制接口。
数据源：配置态为 collectors.toml（顶层 ``enabled`` 名单 + 各采集器段）与
agents.toml（``[agents]`` 段），运行时为 CollectorManager / AgentManager。

路径参数 ``group`` ∈ {"collectors", "agents"}；ComponentSummary.description
由管理器注册填充（空串兜底）。工具不在此管理：工具以"域开关单元"管理
（见 tools API 的 categories 端点）。

配置写盘一律经统一写回器 ``update_config_values``（Schema 校验 + 备份 +
注释重生成），不再做 TOML 原位编辑。
"""

from typing import TYPE_CHECKING, Annotated, Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import update_config_values
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
    build_config_view,
    config_dir,
    get_v2_component_list,
    read_toml_dict,
)
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

logger = get_logger("DashboardComponentsAPI")

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]

# v2：group（路径参数）→ 配置文件名 + enabled 列表在文件内的点分键路径。
# 采集器名单在 collectors.toml 顶层（无嵌套段）；Agent 名单在 [agents] 段内。
_GROUP_TO_FILE: Dict[str, tuple[str, str]] = {
    "collectors": ("collectors.toml", "enabled"),
    "agents": ("agents.toml", "agents.enabled"),
}


def _read_enabled_list(server: "DashboardServer", group: str) -> List[str]:
    """读指定组件组的 enabled 名单（collectors.toml 顶层 / agents.toml [agents]）。"""
    file_name, dotted_key = _GROUP_TO_FILE[group]
    doc = read_toml_dict(config_dir(server) / file_name)
    node: Any = doc
    for part in dotted_key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return []
    return [n for n in node if isinstance(n, str)] if isinstance(node, list) else []


def _sync_enabled_config(server: "DashboardServer", group: str, name: str, *, enable: bool) -> ComponentControlResponse:
    """把组件名加入/移除对应配置文件 enabled 名单（经统一写回器幂等写回）。

    group 映射（v2）：
    - collectors → collectors.toml 顶层 ``enabled``
    - agents → agents.toml ``[agents].enabled``
    """
    if group not in _GROUP_TO_FILE:
        return ComponentControlResponse(success=False, message=f"未知组件组: {group}")
    file_name, dotted_key = _GROUP_TO_FILE[group]

    try:
        enabled = _read_enabled_list(server, group)
        if enable and name not in enabled:
            enabled.append(name)
        elif not enable and name in enabled:
            enabled.remove(name)
        try:
            update_config_values(config_dir(server), file_name, {dotted_key: enabled})
        except ConfigValidationError as exc:
            return ComponentControlResponse(success=False, message=f"配置校验失败: {exc}")
        action_text = "加入启用列表" if enable else "从启用列表移除"
        return ComponentControlResponse(
            success=True,
            message=f"组件 {name} 已{action_text}（{file_name}），重启后生效",
        )
    except Exception as e:
        return ComponentControlResponse(success=False, message=f"配置同步失败: {e}")


@router.get("", response_model=ComponentListResponse)
async def list_components(server: ServerDep) -> ComponentListResponse:
    """获取所有组件列表（v2：采集器 / Agent，含未启用组件）"""
    grouped = get_v2_component_list(build_config_view(server), server)
    return ComponentListResponse(
        collectors=grouped["collectors"],
        agents=grouped["agents"],
    )


@router.get("/{group}/{name}", response_model=ComponentDetailResponse)
async def get_component(
    group: str,
    name: str,
    server: ServerDep,
) -> ComponentDetailResponse:
    """获取单个组件详情（group ∈ {collectors, agents}）"""
    if group not in _GROUP_TO_FILE:
        raise HTTPException(status_code=404, detail=f"Unknown component group: {group}")
    grouped = get_v2_component_list(build_config_view(server), server)
    for summary in grouped.get(group, []):
        if summary.name == name:
            return ComponentDetailResponse(component=ComponentDetail(**summary.model_dump()))
    raise HTTPException(status_code=404, detail=f"Component not found: {group}/{name}")


@router.post("/{group}/{name}/control", response_model=ComponentControlResponse)
async def control_component(
    group: str,
    name: str,
    request: ComponentControlRequest,
    server: ServerDep,
) -> ComponentControlResponse:
    """控制组件：优先动态启停（实例→配置），失败回退配置写回（重启后生效）

    group ∈ {"collectors", "agents"}（路径参数统一为 group）。
    """
    if group not in _GROUP_TO_FILE:
        raise HTTPException(status_code=400, detail=f"Invalid group: {group}")

    if request.action == ComponentControlAction.START:
        dyn = await _try_dynamic_start(server, group, name)
        if dyn is not None and dyn.success:
            resp = dyn
        else:
            resp = _sync_enabled_config(server, group, name, enable=True)
    elif request.action == ComponentControlAction.STOP:
        dyn_resp = await _try_dynamic_stop(server, group, name)
        resp = dyn_resp or _sync_enabled_config(server, group, name, enable=False)
    elif request.action == ComponentControlAction.RESTART:
        stop_resp = await _try_dynamic_stop(server, group, name)
        stop_resp = stop_resp or _sync_enabled_config(server, group, name, enable=False)
        if not stop_resp.success:
            return stop_resp
        dyn = await _try_dynamic_start(server, group, name)
        resp = dyn if dyn is not None and dyn.success else _sync_enabled_config(server, group, name, enable=True)
    else:
        return ComponentControlResponse(success=False, message=f"Unknown action: {request.action}")

    if resp.success and server.config_service is not None:
        await server.config_service.reload_config()
    return resp


async def _try_dynamic_start(server: "DashboardServer", group: str, name: str) -> ComponentControlResponse | None:
    """尝试动态启动组件（实例化+注册+start）。返回 None 表示不可动态启动。"""
    sub_cfg = _read_sub_config(server, group, name)

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
            _sync_enabled_config(server, group, name, enable=True)
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
            _sync_enabled_config(server, group, name, enable=True)
            return ComponentControlResponse(success=True, message=f"Agent {name} 已启动并加入启用配置")
        return ComponentControlResponse(success=False, message=f"Agent {name} 启动失败（已尝试动态启用）")

    return None  # 未知 group 或无实例语义：由调用方回退配置写回


async def _try_dynamic_stop(server: "DashboardServer", group: str, name: str) -> ComponentControlResponse | None:
    """尝试动态停止组件（stop+unregister+配置移除）。返回 None 表示无实例可停。"""
    if group == "collectors":
        manager = server.collector_manager
        if manager is None or manager.get_collector_by_name(name) is None:
            return None
        ok = await manager.disable_collector(name)
        if ok:
            _sync_enabled_config(server, group, name, enable=False)
            return ComponentControlResponse(success=True, message=f"组件 {name} 已停止并从启用配置移除")
        return ComponentControlResponse(success=False, message=f"组件 {name} 停止失败")

    if group == "agents":
        manager = server.agent_manager
        if manager is None or manager.get_agent_by_name(name) is None:
            return None
        ok = await manager.disable_agent(name)
        if ok:
            _sync_enabled_config(server, group, name, enable=False)
            return ComponentControlResponse(success=True, message=f"Agent {name} 已停止并从启用配置移除")
        return ComponentControlResponse(success=False, message=f"Agent {name} 停止失败")

    return None


def _read_sub_config(server: "DashboardServer", group: str, name: str) -> dict:
    """读取组件的子段配置 dict（无则空 dict）。

    - collectors → collectors.toml 顶层同名段（``[<name>]``）
    - agents → agents.toml ``[agents.<name>]``
    """
    if group == "collectors":
        doc = read_toml_dict(config_dir(server) / "collectors.toml")
        section = doc.get(name)
        return dict(section) if isinstance(section, dict) else {}
    if group == "agents":
        main_config = server.config_service.main_config if server.config_service else {}
        agents_section = main_config.get("agents") if isinstance(main_config, dict) else {}
        section = (agents_section or {}).get(name) if isinstance(agents_section, dict) else None
        return dict(section) if isinstance(section, dict) else {}
    return {}
