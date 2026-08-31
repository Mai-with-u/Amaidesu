"""Agenda 工作台 API（v2 Agenda 子系统）

提供主播 Agent 节目单（Agenda）状态查询与手动控制端点：

- ``GET  /api/v1/agenda/state``   — 整场快照 + 推进历史 + 环节清单 + 扩展缓存
- ``POST /api/v1/agenda/control`` — pause / resume / skip / rewind / jump / unload / start

数据来源
--------
- 运行时态 → :class:`StreamerAgent` 公开门面（``get_agenda_view`` / ``agenda_control``），
  避免 Dashboard 直接触碰 ``_agenda_state`` / ``_agenda_loader`` 等私有属性。
- 配置只读展示 → ``server.config_service.main_config["agents"]["streamer"]``，
  与 components.py 的 main_config 读取方式一致。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Callable, Dict, Optional, Protocol, cast

from fastapi import APIRouter, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.agenda import (
    AgendaConfigView,
    AgendaControlRequest,
    AgendaControlResponse,
    AgendaExpandedView,
    AgendaSegmentView,
    AgendaStateResponse,
)

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer


router = APIRouter()

# 类型别名：FastAPI 依赖注入的 DashboardServer
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


class _AgendaControlCallable(Protocol):
    """StreamerAgent.agenda_control 的鸭子接口（由 facade 实现）。

    Dashboard 只依赖这一最小契约，agent_manager 返回的实例若满足该契约即可
    被本模块消费；不强制继承 StreamerAgent。
    """

    async def __call__(
        self,
        action: str,
        *,
        segment_id: Optional[str] = None,
        path: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]: ...


# ---------------------------------------------------------------------------
# 内部辅助：定位 StreamerAgent + 解析 agenda 配置
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


def _read_streamer_agenda_config(server: "DashboardServer") -> Dict[str, Any]:
    """从 main_config 读 agents.streamer.agenda_* 字段；缺字段用默认值。"""
    main_config = server.config_service.main_config if server.config_service else {}
    streamer_cfg = ((main_config or {}).get("agents") or {}).get("streamer") or {}
    # 兼容旧字段 outline_*
    agenda_enabled = bool(streamer_cfg.get("agenda_enabled", streamer_cfg.get("outline_enabled", False)))
    agenda_path = str(streamer_cfg.get("agenda_path", streamer_cfg.get("outline_path", "")) or "")
    agenda_auto_start = bool(streamer_cfg.get("agenda_auto_start", streamer_cfg.get("outline_auto_start", True)))
    return {
        "agenda_enabled": agenda_enabled,
        "agenda_path": agenda_path,
        "agenda_auto_start": agenda_auto_start,
    }


def _empty_state_response(
    server: "DashboardServer",
    *,
    message: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> AgendaStateResponse:
    """构造 ``available=false`` 的降级响应（空快照/空列表，配置尽力填充）。"""
    cfg_dict = cfg if cfg is not None else _read_streamer_agenda_config(server)
    return AgendaStateResponse(
        available=False,
        message=message,
        snapshot=None,
        transitions=[],
        segments=[],
        expanded={},
        config=AgendaConfigView(**cfg_dict),
    )


def _build_agenda_view(view: Dict[str, Any]) -> AgendaStateResponse:
    """把 StreamerAgent.get_agenda_view() 的 dict 包成强类型响应。"""
    segments = [AgendaSegmentView(**s) for s in view.get("segments", [])]
    expanded_raw = view.get("expanded", {}) or {}
    expanded_typed: Dict[str, Optional[AgendaExpandedView]] = {}
    for seg_id, value in expanded_raw.items():
        expanded_typed[seg_id] = AgendaExpandedView(**value) if isinstance(value, dict) else None
    return AgendaStateResponse(
        available=True,
        message=None,
        snapshot=view.get("snapshot"),
        transitions=list(view.get("transitions", [])),
        segments=segments,
        expanded=expanded_typed,
        config=AgendaConfigView(),  # available=true 时 config 由 outer 覆盖
    )


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/state", response_model=AgendaStateResponse)
async def get_agenda_state(server: ServerDep) -> AgendaStateResponse:
    """获取 Agenda 工作台状态视图（只读）。

    当 streamer agent 未注册、Agenda 未启用或组件未就绪时返回 ``available=false``
    降级响应（snapshot/transitions/segments/expanded 均为空，config 尽力填充）。
    """
    cfg = _read_streamer_agenda_config(server)
    agent = _resolve_streamer_agent(server)
    if agent is None:
        return _empty_state_response(server, message="主播 Agent 未启用", cfg=cfg)
    if not cfg["agenda_enabled"]:
        return _empty_state_response(server, message="Agenda 未开启", cfg=cfg)

    is_available = getattr(agent, "is_agenda_available", None)
    if callable(is_available):
        try:
            if not is_available():
                return _empty_state_response(server, message="Agenda 组件未就绪", cfg=cfg)
        except Exception:
            return _empty_state_response(server, message="Agenda 组件未就绪", cfg=cfg)

    view_getter_raw = getattr(agent, "get_agenda_view", None)
    if not callable(view_getter_raw):
        return _empty_state_response(server, message="Agenda 组件未就绪", cfg=cfg)
    view_getter = cast(Callable[..., Optional[Dict[str, Any]]], view_getter_raw)

    try:
        view: Optional[Dict[str, Any]] = view_getter()
    except Exception as exc:
        return _empty_state_response(server, message=f"Agenda 视图获取失败: {exc}", cfg=cfg)

    if view is None:
        return _empty_state_response(server, message="Agenda 组件未就绪", cfg=cfg)

    response = _build_agenda_view(view)
    response.config = AgendaConfigView(**cfg)
    return response


@router.post("/control", response_model=AgendaControlResponse)
async def control_agenda(
    request: AgendaControlRequest,
    server: ServerDep,
) -> AgendaControlResponse:
    """执行 Agenda 控制动作（pause/resume/skip/rewind/jump/unload/start）。

    错误约定（不抛 HTTPException）：
    - agent 未注册 / Agenda 未开启 / 组件未就绪 → ``success=false`` + 原因
    - jump 缺 ``segment_id`` / start 缺 ``path`` → ``success=false`` + 字段校验消息
    - start TOML 解析失败 → ``success=false`` + 异常消息（捕获 FileNotFoundError /
      PermissionError / ValidationError 等）
    - state 控制方法抛异常 → ``success=false`` + 异常消息
    """
    agent = _resolve_streamer_agent(server)
    if agent is None:
        return AgendaControlResponse(success=False, message="主播 Agent 未启用", snapshot=None)
    cfg = _read_streamer_agenda_config(server)
    if not cfg["agenda_enabled"]:
        return AgendaControlResponse(success=False, message="Agenda 未开启", snapshot=None)

    control_raw = getattr(agent, "agenda_control", None)
    if not callable(control_raw):
        return AgendaControlResponse(success=False, message="Agenda 组件未就绪", snapshot=None)
    control = cast(_AgendaControlCallable, control_raw)

    action_value = request.action.value
    try:
        ok, message, snapshot = await control(
            action_value,
            segment_id=request.segment_id,
            path=request.path,
        )
    except Exception as exc:
        return AgendaControlResponse(
            success=False,
            message=f"控制失败: {exc}",
            snapshot=None,
        )
    return AgendaControlResponse(success=ok, message=message, snapshot=snapshot)
