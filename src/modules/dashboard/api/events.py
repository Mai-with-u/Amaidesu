"""
事件历史 API

提供事件历史的查询接口，从 EventHistoryService 环形缓冲中读取。
"""

from typing import TYPE_CHECKING, Annotated, Optional

from fastapi import APIRouter, Depends, Query

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("EventsAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/events")
async def list_events(
    limit: int = Query(50, ge=1, le=500, description="最大返回条数"),
    type: Optional[str] = Query(None, description="事件类型筛选，逗号分隔"),
    level: Optional[str] = Query(None, description="严重级别筛选"),
    before_timestamp: Optional[float] = Query(None, description="游标：返回此时间戳之前的事件"),
    since_id: Optional[str] = Query(None, description="游标：返回此事件 id 之后的事件（断线/刷新续传）"),
    server: ServerDep = ...,
):
    """获取事件历史（基于内存环形缓冲，支持筛选和游标分页）"""
    history_service = server.event_history
    if not history_service:
        return {"events": [], "total": 0, "has_more": False}

    if since_id:
        # 游标续传：返回 since_id 之后的缺口（旧→新）；游标未命中退化为最近窗口，
        # 客户端按 id 去重合并
        gap = history_service.get_since(since_id, limit=limit)
        return {"events": [e.model_dump() for e in gap], "total": len(gap), "has_more": False}

    types_list = type.split(",") if type else None
    events = history_service.query(
        types=types_list,
        level=level,
        before_timestamp=before_timestamp,
        limit=limit + 1,
    )

    has_more = len(events) > limit
    if has_more:
        events = events[:limit]

    return {
        "events": [e.model_dump() for e in events],
        "total": len(events),
        "has_more": has_more,
    }


@router.get("/events/stats")
async def get_event_stats(
    server: ServerDep = ...,
):
    """获取事件统计信息"""
    history_service = server.event_history
    if not history_service:
        return {"total": 0, "by_type": {}, "by_level": {}}

    return history_service.get_statistics()
