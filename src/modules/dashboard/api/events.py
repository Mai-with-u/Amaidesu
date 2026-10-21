"""
事件历史 API

事件日志页的查询接口：内存环形缓冲 ∪ 诊断事件流文件（logs/events），
支持类型/级别筛选、时间窗与双向游标翻页。``since_id`` 游标续传分支维持
内存环形读取（断线补缺口只发生在本次运行内）。
"""

import asyncio
from typing import TYPE_CHECKING, Annotated, Optional

from fastapi import APIRouter, Depends, Query

from src.modules.dashboard.api.system import startup_time_ms
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.event import EventListResponse
from src.modules.dashboard.services.event_query import EventQueryReader, events_dir_of
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("EventsAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.get("/events", response_model=EventListResponse)
async def list_events(
    server: ServerDep,
    # le=1000 对齐前端 events store 的 MAX_EVENTS：带游标拉缺口固定传 1000，
    # 上限低于它即整请求 422（内存环形缓冲读取，放宽无成本）
    limit: int = Query(50, ge=1, le=1000, description="最大返回条数"),
    type: Optional[str] = Query(None, description="事件类型筛选，逗号分隔"),
    level: Optional[str] = Query(None, description="严重级别筛选"),
    start_ms: Optional[int] = Query(None, ge=0, description="窗口起点（Unix 毫秒）；缺省=本次启动时刻"),
    before_timestamp_ms: Optional[int] = Query(None, ge=0, description="游标：返回此时间戳（Unix 毫秒）之前的事件"),
    take: str = Query(
        "newest", pattern="^(newest|oldest)$", description="取窗口内最靠新（newest）还是最靠旧（oldest）的一页"
    ),
    since_id: Optional[str] = Query(None, description="游标：返回此事件 id 之后的事件（断线/刷新续传）"),
) -> EventListResponse:
    """获取事件历史（内存环形 ∪ 落盘文件，支持筛选和游标分页）。"""
    history_service = server.event_history
    if not history_service:
        return EventListResponse()

    if since_id:
        # 游标续传：返回 since_id 之后的缺口（旧→新）；游标未命中退化为最近窗口，
        # 客户端按 id 去重合并
        gap = history_service.get_since(since_id, limit=limit)
        return EventListResponse(events=[e.model_dump() for e in gap], total=len(gap), has_more=False)

    types_list = type.split(",") if type else None
    effective_start = start_ms if start_ms is not None else startup_time_ms()
    reader = EventQueryReader(history_service, events_dir_of(server))
    # 文件扫描是同步 IO，放线程池避免阻塞事件循环（低频诊断操作，无缓存）
    window = await asyncio.to_thread(
        reader.query,
        start_ms=effective_start,
        end_ms=now_ms(),
        before_timestamp_ms=before_timestamp_ms,
        types=types_list,
        level=level,
        take=take,
        limit=limit,
    )
    return EventListResponse(
        events=[record.model_dump() for record in window.records],
        total=len(window.records),
        has_more=window.has_more_older,
        has_more_newer=window.has_more_newer,
        files_pruned=window.files_pruned,
        window_start_ms=effective_start,
    )
