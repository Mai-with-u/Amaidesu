"""
Agent 控制面 API（运行态观测 + 框架级控制端点）

- GET  /api/v1/agents                  -> 已注册 Agent 列表（运行态 + enabled 配置态）
- GET  /api/v1/agents/{name}/state     -> 单个 Agent 运行状态
- POST /api/v1/agents/{name}/control   -> 框架级控制（pause/resume/shutdown/restart）
- POST /api/v1/agents/{name}/prompt    -> 递话（运营提醒/插话：纯文本留言，不派新任务）

数据源：``DashboardServer.agent_control``（AgentControl 直调接口，构造时由
agent_manager 生成）。enabled 标记读 agents.toml ``[agents].enabled`` 名单
（与组件管理 API 同源）。

动作语义：pause/resume 即时生效且不要求确认；shutdown（停机）与 restart
（停用后经 enable_agent 同一构造路径重建并启动）属高风险动作，缺
``confirm=true`` 时以 400 拒绝并附中文风险说明。未知动作 400，未知
Agent 404。
"""

from typing import TYPE_CHECKING, Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.agent import (
    AgentControlAction,
    AgentControlRequest,
    AgentControlResponse,
    AgentListResponse,
    AgentPromptRequest,
    AgentPromptResponse,
    AgentStateResponse,
    AgentSummary,
)
from src.modules.dashboard.utils.component_helper import config_dir, read_toml_dict
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.agents.control import AgentControl
    from src.modules.dashboard.server import DashboardServer
    from src.modules.agents.manager import AgentManager

logger = get_logger("DashboardAgentsAPI")

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]

# 高风险动作（停机 / 重建）：缺显式确认时拒绝执行。
_RISKY_ACTIONS = (AgentControlAction.SHUTDOWN, AgentControlAction.RESTART)


def _get_agent_control(server: "DashboardServer") -> "AgentControl":
    """取 AgentControl；未注入（极简启动/测试场景）时抛 503。"""
    control = getattr(server, "agent_control", None)
    if control is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AgentManager 未注入，Agent 控制面不可用",
        )
    return control


def _get_agent_manager(server: "DashboardServer") -> Optional["AgentManager"]:
    """取 AgentManager（descriptions / rebuild 数据源）；未注入时返回 None。"""
    return getattr(server, "agent_manager", None)


def _read_enabled_names(server: "DashboardServer") -> List[str]:
    """读 agents.toml ``[agents].enabled`` 名单（不可用时按空名单兜底）。"""
    try:
        doc = read_toml_dict(config_dir(server) / "agents.toml")
        agents_section = doc.get("agents")
        raw = agents_section.get("enabled") if isinstance(agents_section, dict) else None
        if isinstance(raw, list):
            return [n for n in raw if isinstance(n, str)]
    except Exception as exc:  # noqa: BLE001 - 只读边界：配置不可用按空名单
        logger.debug(f"读取 agents.toml enabled 名单失败，按空名单处理: {exc}")
    return []


@router.get("", response_model=AgentListResponse, summary="列出已注册 Agent（运行态 + enabled 配置态）")
async def list_agents(server: ServerDep) -> AgentListResponse:
    """Agent 运行名册全量视图：每项含状态、心跳、存活、重启计数与 enabled 标记。"""
    control = _get_agent_control(server)
    manager = _get_agent_manager(server)
    enabled_names = set(_read_enabled_names(server))
    descriptions = manager.descriptions if manager is not None else {}
    items: List[AgentSummary] = []
    for name in control.list_agents():
        info = control.state_of(name)
        if info is None:
            continue
        items.append(
            AgentSummary(
                name=name,
                description=descriptions.get(name, ""),
                state=str(info["state"]),
                heartbeat_ms=int(info["heartbeat_ms"]),
                is_alive=bool(info["is_alive"]),
                restart_count=int(info["restart_count"]),
                enabled=name in enabled_names,
            )
        )
    return AgentListResponse(agents=items)


@router.get("/{name}/state", response_model=AgentStateResponse, summary="查询单个 Agent 运行状态")
async def get_agent_state(name: str, server: ServerDep) -> AgentStateResponse:
    """单个 Agent 的 state / heartbeat_ms / is_alive / restart_count。"""
    control = _get_agent_control(server)
    manager = _get_agent_manager(server)
    info = control.state_of(name)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Agent 不存在: {name}")
    descriptions = manager.descriptions if manager is not None else {}
    return AgentStateResponse(
        name=name,
        description=descriptions.get(name, ""),
        state=str(info["state"]),
        heartbeat_ms=int(info["heartbeat_ms"]),
        is_alive=bool(info["is_alive"]),
        restart_count=int(info["restart_count"]),
    )


@router.post(
    "/{name}/control", response_model=AgentControlResponse, summary="框架级控制（pause/resume/shutdown/restart）"
)
async def control_agent(name: str, request: AgentControlRequest, server: ServerDep) -> AgentControlResponse:
    """对指定 Agent 执行框架级控制动作。

    - pause / resume：即时生效，不要求 confirm
    - shutdown / restart：高风险（停机 / 停用后经统一构造路径重建），缺
      ``confirm=true`` 时 400 并附中文风险说明，确认受理返回 202
    - restart 走 ``AgentManager.rebuild``（与守护自愈同一重建路径，重启
      计数跨实例继承 +1）

    未知动作 400；Agent 不在名册 404；动作执行失败 500。
    """
    control = _get_agent_control(server)
    manager = _get_agent_manager(server)
    try:
        action = AgentControlAction(request.action)
    except ValueError as exc:
        valid = "/".join(a.value for a in AgentControlAction)
        raise HTTPException(status_code=400, detail=f"未知控制动作: {request.action}（可选: {valid}）") from exc
    if control.state_of(name) is None:
        raise HTTPException(status_code=404, detail=f"Agent 不存在: {name}")
    if action in _RISKY_ACTIONS and not request.confirm:
        risk = (
            "停机后该 Agent 不再响应（需重新启用才能恢复）"
            if action is AgentControlAction.SHUTDOWN
            else "重建会停止当前实例并按启用配置重新构造启动"
        )
        raise HTTPException(
            status_code=400,
            detail=f"{action.value} 是高风险动作（{risk}）。如确认执行，请携带 confirm: true 重新提交。",
        )

    if action is AgentControlAction.PAUSE:
        ok = await control.pause(name)
        action_text = "已暂停"
    elif action is AgentControlAction.RESUME:
        ok = await control.resume(name)
        action_text = "已恢复"
    elif action is AgentControlAction.SHUTDOWN:
        ok = await control.shutdown(name)
        action_text = "已停机"
    else:
        if manager is None:
            raise HTTPException(status_code=503, detail="AgentManager 未注入，restart 不可用")
        ok = await manager.rebuild(name)
        action_text = "已重建并启动"

    if not ok:
        raise HTTPException(status_code=500, detail=f"Agent {name} 执行 {action.value} 失败")
    info = control.state_of(name)
    payload = AgentControlResponse(
        success=True,
        action=action.value,
        name=name,
        message=f"Agent {name} {action_text}",
        state=str(info["state"]) if info else None,
    )
    if action in _RISKY_ACTIONS:
        # 破坏性动作为受理语义（停机/重建异步落地），返回 202；响应体与 200 同形
        return JSONResponse(status_code=202, content=payload.model_dump(mode="json"))
    return payload


@router.post("/{name}/prompt", response_model=AgentPromptResponse, summary="向 Agent 递话（运营提醒/插话）")
async def prompt_agent(name: str, request: AgentPromptRequest, server: ServerDep) -> AgentPromptResponse:
    """给指定 Agent 发纯文本留言（不派新任务、不进任务账本）。

    source 固定记 "operator"（运营通道）。目标执行中的任务下一步吸收，
    挂起中的被唤醒重新判断。Agent 不在名册 404；目标拒收（未实现消化
    通道或留言队列已满）409。
    """
    control = _get_agent_control(server)
    result = await control.prompt(name, request.content, source="operator")
    if not result["ok"]:
        code = 404 if result["error"] == "not_found" else 409
        raise HTTPException(status_code=code, detail=result["message"])
    return AgentPromptResponse(delivered=bool(result["delivered"]))
