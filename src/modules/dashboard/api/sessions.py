"""直播场次 API

提供场次的查询与生命周期控制（``/api/v1/live-sessions/*``；
与 ContextService 的 ``/sessions`` 会话列表互不相干）：
- ``GET  /sessions``           场次列表（倒序 + 消息数，供控制台场次侧边栏）
- ``POST /sessions/open``      开启新场次（进行中场次先自动结束）
- ``POST /sessions/{id}/close`` 结束指定场次（须为当前进行中场次）
- ``DELETE /sessions/{id}``    删除场次（级联清除明细；临时场次删除后自动重建）

场次归属由 ``LiveSessionManager`` 负责（唯一事实源）；本层只做参数校验与
结果序列化，不含场次语义。
"""

from typing import TYPE_CHECKING, Annotated, Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("SessionsAPI")

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


class SessionItem(BaseModel):
    """场次列表条目"""

    live_session_id: int = Field(..., description="场次主键（live_sessions.id）")
    source: str = Field(default="manual", description="场次来源：manual / replay / scratch / legacy")
    title: Optional[str] = Field(default=None, description="场次标题")
    room_id: str = Field(default="", description="房间/频道标识（普通属性）")
    platform: str = Field(default="", description="平台标识")
    started_at_ms: int = Field(..., description="开始时刻（Unix 毫秒）")
    ended_at_ms: Optional[int] = Field(default=None, description="结束时刻（Unix 毫秒）；NULL=进行中")
    message_count: int = Field(default=0, description="本场次消息数（live_chat 行数）")
    is_active: bool = Field(default=False, description="是否为当前进行中的显式场次")


class SessionListResponse(BaseModel):
    """场次列表响应"""

    items: List[SessionItem] = Field(default_factory=list)
    active_session_id: Optional[int] = Field(default=None, description="当前进行中的显式场次主键")


class SessionOpenRequest(BaseModel):
    """开启场次请求"""

    title: Optional[str] = Field(default=None, description="场次标题（可选）")
    room_id: Optional[str] = Field(default=None, description="房间/频道标识（可选，缺省用装配默认值）")
    platform: Optional[str] = Field(default=None, description="平台标识（可选，缺省用装配默认值）")


class SessionOpenResponse(BaseModel):
    """开启场次响应"""

    live_session_id: int = Field(..., description="新场次主键")


class SessionActionResponse(BaseModel):
    """场次动作通用响应"""

    success: bool = Field(default=True, description="动作是否成功")
    detail: str = Field(default="", description="动作结果说明")


def _require_session_manager(server: "DashboardServer"):
    """取 session_manager，未装配时返回 None。"""
    return getattr(server, "session_manager", None)


def _row_to_item(row: Any, active_pk: Optional[int]) -> SessionItem:
    """live_sessions 行 → 列表条目。"""
    ended = row["ended_at_ms"]
    return SessionItem(
        live_session_id=int(row["id"]),
        source=str(row["source"] or "manual"),
        title=row["title"],
        room_id=str(row["stream_id"] or ""),
        platform=str(row["platform"] or ""),
        started_at_ms=int(row["started_at_ms"]),
        ended_at_ms=int(ended) if ended is not None else None,
        message_count=int(row["message_count"] or 0) if "message_count" in row.keys() else 0,
        is_active=active_pk is not None and int(row["id"]) == active_pk,
    )


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    server: ServerDep,
    limit: Annotated[int, Query(ge=1, le=200, description="最多返回条数")] = 50,
) -> SessionListResponse:
    """列出直播场次（按开始时间倒序，附消息数）。"""
    manager = _require_session_manager(server)
    if manager is None:
        raise HTTPException(status_code=503, detail="LiveSessionManager 未装配")
    rows = await manager.list_sessions(limit=limit)
    active_pk = manager.active_pk
    return SessionListResponse(
        items=[_row_to_item(row, active_pk) for row in rows],
        active_session_id=active_pk,
    )


@router.post("/open", response_model=SessionOpenResponse)
async def open_session(request: SessionOpenRequest, server: ServerDep) -> SessionOpenResponse:
    """开启新直播场次（已有机行进行中时先自动结束）。"""
    manager = _require_session_manager(server)
    if manager is None:
        raise HTTPException(status_code=503, detail="LiveSessionManager 未装配")
    pk = await manager.open_session(
        title=request.title,
        room_id=request.room_id,
        platform=request.platform,
        source="manual",
    )
    return SessionOpenResponse(live_session_id=pk)


@router.post("/{session_id}/close", response_model=SessionActionResponse)
async def close_session(session_id: int, server: ServerDep) -> SessionActionResponse:
    """结束当前进行中的场次（指定 id 必须与进行中场次一致）。"""
    manager = _require_session_manager(server)
    if manager is None:
        raise HTTPException(status_code=503, detail="LiveSessionManager 未装配")
    if manager.active_pk is None:
        return SessionActionResponse(success=False, detail="无进行中的显式场次")
    if manager.active_pk != session_id:
        raise HTTPException(
            status_code=409,
            detail=f"指定场次 {session_id} 不是当前进行中场次（当前：{manager.active_pk}）",
        )
    closed = await manager.close_session(reason="API 手动结束")
    return SessionActionResponse(success=closed, detail="场次已结束" if closed else "结束失败")


@router.delete("/{session_id}", response_model=SessionActionResponse)
async def delete_session(session_id: int, server: ServerDep) -> SessionActionResponse:
    """删除场次（级联清除明细数据；进行中场次先自动结束）。"""
    manager = _require_session_manager(server)
    if manager is None:
        raise HTTPException(status_code=503, detail="LiveSessionManager 未装配")
    deleted = await manager.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"场次不存在: {session_id}")
    return SessionActionResponse(success=True, detail=f"场次 {session_id} 已删除（明细级联清除）")


__all__ = ["router"]
