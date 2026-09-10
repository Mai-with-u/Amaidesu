"""流程单（Rundown）编排页 API

提供主播 Agent 流程单状态查询与手动控制端点：

- ``GET  /api/v1/agenda/state``   — 整场快照 + 变更历史 + 环节清单
- ``POST /api/v1/agenda/control`` — pause / resume / next / goto

数据来源
--------
- 运行时态 → :class:`StreamerAgent` 公开门面（``get_rundown_view`` / ``rundown_control``），
  避免 Dashboard 直接触碰 ``_rundown_state`` 等私有属性。
- 配置只读展示 → ``server.config_service.main_config["agents"]["streamer"]``，
  与 components.py 的 main_config 读取方式一致。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Callable, Dict, Optional, Protocol, cast

from fastapi import APIRouter, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.agenda import (
    RundownConfigView,
    RundownControlRequest,
    RundownControlResponse,
    RundownSegmentView,
    RundownStateResponse,
)

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer


router = APIRouter()

# 类型别名：FastAPI 依赖注入的 DashboardServer
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


class _RundownControlCallable(Protocol):
    """StreamerAgent.rundown_control 的鸭子接口（由 facade 实现）。

    Dashboard 只依赖这一最小契约，agent_manager 返回的实例若满足该契约即可
    被本模块消费；不强制继承 StreamerAgent。
    """

    async def __call__(
        self,
        action: str,
        *,
        segment_id: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]: ...


# ---------------------------------------------------------------------------
# 内部辅助：定位 StreamerAgent + 解析流程单配置
# ---------------------------------------------------------------------------


def _resolve_streamer_agent(server: "DashboardServer") -> Optional[Any]:
    """从 agent_manager 中取出 ``streamer`` 实例；无则返回 None。"""
    am = getattr(server, "agent_manager", None)
    if am is None:
        return None
    getter = getattr(am, "get_agent_by_name", None) or getattr(am, "get", None)
    if getter is None:
        return None
    try:
        return getter("streamer")
    except Exception:
        return None


def _read_streamer_rundown_config(server: "DashboardServer") -> Dict[str, Any]:
    """从 main_config 读 agents.streamer.rundown_id；缺字段用默认值。"""
    main_config = server.config_service.main_config if server.config_service else {}
    streamer_cfg = ((main_config or {}).get("agents") or {}).get("streamer") or {}
    return {"rundown_id": str(streamer_cfg.get("rundown_id", "") or "")}


def _empty_state_response(
    server: "DashboardServer",
    *,
    message: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> RundownStateResponse:
    """构造 ``available=false`` 的降级响应（空快照/空列表，配置尽力填充）。"""
    cfg_dict = cfg if cfg is not None else _read_streamer_rundown_config(server)
    return RundownStateResponse(
        available=False,
        message=message,
        snapshot=None,
        transitions=[],
        segments=[],
        config=RundownConfigView(**cfg_dict),
    )


def _build_rundown_view(view: Dict[str, Any]) -> RundownStateResponse:
    """把 StreamerAgent.get_rundown_view() 的 dict 包成强类型响应。"""
    segments = [RundownSegmentView(**s) for s in view.get("segments", [])]
    return RundownStateResponse(
        available=True,
        message=None,
        snapshot=view.get("snapshot"),
        transitions=list(view.get("transitions", [])),
        segments=segments,
        config=RundownConfigView(),  # available=true 时 config 由 outer 覆盖
    )


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/state", response_model=RundownStateResponse)
async def get_rundown_state(server: ServerDep) -> RundownStateResponse:
    """获取流程单编排页状态视图（只读）。

    当 streamer agent 未注册或流程单未加载时返回 ``available=false``
    降级响应（snapshot/transitions/segments 均为空，config 尽力填充）。
    """
    cfg = _read_streamer_rundown_config(server)
    agent = _resolve_streamer_agent(server)
    if agent is None:
        return _empty_state_response(server, message="主播 Agent 未启用", cfg=cfg)

    is_available = getattr(agent, "is_rundown_available", None)
    if callable(is_available):
        try:
            if not is_available():
                return _empty_state_response(server, message="流程单未加载", cfg=cfg)
        except Exception:
            return _empty_state_response(server, message="流程单未加载", cfg=cfg)

    view_getter_raw = getattr(agent, "get_rundown_view", None)
    if not callable(view_getter_raw):
        return _empty_state_response(server, message="流程单未加载", cfg=cfg)
    view_getter = cast(Callable[..., Optional[Dict[str, Any]]], view_getter_raw)

    try:
        view: Optional[Dict[str, Any]] = view_getter()
    except Exception as exc:
        return _empty_state_response(server, message=f"流程单视图获取失败: {exc}", cfg=cfg)

    if view is None:
        return _empty_state_response(server, message="流程单未加载", cfg=cfg)

    response = _build_rundown_view(view)
    response.config = RundownConfigView(**cfg)
    return response


@router.post("/control", response_model=RundownControlResponse)
async def control_rundown(
    request: RundownControlRequest,
    server: ServerDep,
) -> RundownControlResponse:
    """执行流程单控制动作（pause/resume/next/goto，by="human"）。

    错误约定（不抛 HTTPException）：
    - agent 未注册 / 组件未就绪 → ``success=false`` + 原因
    - goto 缺 ``segment_id`` → ``success=false`` + 字段校验消息
    - 状态机结构化拒绝（最少停留未到等）→ ``success=false`` + 拒绝原因
    """
    agent = _resolve_streamer_agent(server)
    if agent is None:
        return RundownControlResponse(success=False, message="主播 Agent 未启用", snapshot=None)

    control_raw = getattr(agent, "rundown_control", None)
    if not callable(control_raw):
        return RundownControlResponse(success=False, message="流程单未加载", snapshot=None)
    control = cast(_RundownControlCallable, control_raw)

    action_value = request.action.value
    try:
        ok, message, snapshot = await control(
            action_value,
            segment_id=request.segment_id,
        )
    except Exception as exc:
        return RundownControlResponse(
            success=False,
            message=f"控制失败: {exc}",
            snapshot=None,
        )
    return RundownControlResponse(success=ok, message=message, snapshot=snapshot)
