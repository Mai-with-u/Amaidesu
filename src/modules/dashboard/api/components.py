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

from src.modules.agents.companions import owner_of
from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import update_config_values
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.component import (
    ComponentControlAction,
    ComponentControlRequest,
    ComponentControlResponse,
    ComponentListResponse,
)
from src.modules.dashboard.services.component_runtime import (
    ComponentRuntimeContext,
    read_sub_config,
    start_instance,
    stop_instance,
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
    # 附属采集器随所属 Agent 开关，单独开关没有意义（Agent 不在时只会空转重试），也不该写进 collectors.toml
    owner = owner_of(name) if group == "collectors" else None
    if owner is not None:
        return ComponentControlResponse(
            success=False,
            message=f"采集器 {name} 随 Agent {owner} 一同开关，请开关 Agent {owner}",
        )

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
    """尝试动态启动组件（实例化+注册+start）。返回 None 表示不可动态启动。

    实际管理器操作复用服务层 ``component_runtime``（与配置保存热应用同一份
    逻辑）；本函数只补按钮路径特有的语义：新实例化时同步 enabled 配置、
    响应措辞。未知采集器名的拦截已内聚在服务层启动路径。
    """
    ctx = ComponentRuntimeContext.from_server(server)
    sub_cfg = read_sub_config(server.config_service, config_dir(server), group, name)
    result = await start_instance(ctx, group, name, sub_cfg)
    if result is None:
        return None  # 无管理器：由调用方回退配置写回
    label = "Agent" if group == "agents" else "组件"
    if not result.success:
        return ComponentControlResponse(success=False, message=f"{label} {name} 启动失败：{result.detail}")
    if result.created:
        _sync_enabled_config(server, group, name, enable=True)
        return ComponentControlResponse(success=True, message=f"{label} {name} 已启动并加入启用配置")
    return ComponentControlResponse(success=True, message=f"{label} {name} 已启动")


async def _try_dynamic_stop(server: "DashboardServer", group: str, name: str) -> ComponentControlResponse | None:
    """尝试动态停止组件（stop+unregister+配置移除）。返回 None 表示无实例可停。

    管理器操作复用服务层 ``component_runtime``；按钮停用路径连带停 Agent 的
    附属采集器（服务层默认行为）。
    """
    ctx = ComponentRuntimeContext.from_server(server)
    ok = await stop_instance(ctx, group, name)
    if ok is None:
        return None
    label = "Agent" if group == "agents" else "组件"
    if ok:
        _sync_enabled_config(server, group, name, enable=False)
        return ComponentControlResponse(success=True, message=f"{label} {name} 已停止并从启用配置移除")
    return ComponentControlResponse(success=False, message=f"{label} {name} 停止失败")
