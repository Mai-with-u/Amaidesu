"""观众统计只读端点

viewers 表的唯一消费面：按排序白名单取 top-N 汇总行，
``count`` 为返回行数（受 limit 约束，非全表行数）。
"""

from typing import TYPE_CHECKING, Annotated, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.modules.dashboard.dependencies import get_dashboard_server

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


class ViewerStatItem(BaseModel):
    """单行观众统计（viewers 表行的投影）。"""

    user_id: str
    user_name: str
    message_count: int
    gift_count: int
    replied_count: int
    interaction_count: int
    last_active_ms: int


class ViewerStatsResponse(BaseModel):
    """观众统计响应：``count`` = ``top`` 行数。"""

    count: int
    top: List[ViewerStatItem]


@router.get("", response_model=ViewerStatsResponse)
async def get_viewer_stats(
    server: ServerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 5,
    order_by: str = "message_count",
) -> ViewerStatsResponse:
    """列出观众统计排行（只读）。

    ``order_by`` 合法性由存储层白名单兜底，非法值转 400。
    """
    viewer_repo = server.viewer_repo
    if viewer_repo is None:
        raise HTTPException(status_code=503, detail="观众统计仓储不可用")

    try:
        rows = await viewer_repo.list_viewer_stats(limit=limit, order_by=order_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    items = [
        ViewerStatItem(
            user_id=row["user_id"],
            user_name=row["user_name"],
            message_count=row["message_count"],
            gift_count=row["gift_count"],
            replied_count=row["replied_count"],
            interaction_count=row["interaction_count"],
            last_active_ms=row["last_active_ms"],
        )
        for row in rows
    ]
    return ViewerStatsResponse(count=len(items), top=items)
