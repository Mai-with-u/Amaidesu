"""观众数据只读端点

观众域的 WebUI 消费面：``viewers`` 统计表 + 明细三表（live_chat / gifts /
super_chats）按 ``user_id`` 的聚合查询，全部只读。

- ``GET /viewers``                      观众列表（搜索 / 排序 / 分页，``total`` 全计数）
- ``GET /viewers/insights``             互动分析聚合（活跃分桶 / 回复覆盖 / 按天弹幕量）
- ``GET /viewers/{user_id}``            单观众档案（统计 + 活跃边界 + 贡献汇总）
- ``GET /viewers/{user_id}/messages``   对话批次（观众消息与主播回复交织，游标分页）
- ``GET /viewers/{user_id}/contributions`` 礼物与 SC 明细 + 汇总
- ``GET /viewers/{user_id}/sessions``   参与场次聚合

数据现实：礼物事件无金额，礼物维度以件数计；上舰（guard）是 live_chat 文本行，
进房 / 关注 / 点赞不落库——页面如实呈现这些边界，不臆造数据。
"""

from typing import TYPE_CHECKING, Annotated, Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


def _require_viewer_repo(server: "DashboardServer") -> Any:
    """取观众统计仓储，未装配时 503。"""
    repo = server.viewer_repo
    if repo is None:
        raise HTTPException(status_code=503, detail="观众统计仓储不可用")
    return repo


def _require_chat_repo(server: "DashboardServer") -> Any:
    """取直播明细仓储，未装配时 503。"""
    repo = getattr(server, "chat_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="直播明细仓储不可用")
    return repo


class ViewerListItem(BaseModel):
    """单行观众统计（viewers 表行的投影）。"""

    user_id: str
    user_name: str
    message_count: int
    gift_count: int
    replied_count: int
    interaction_count: int
    last_active_ms: int


class ViewerListResponse(BaseModel):
    """观众列表响应：``total`` 为命中搜索条件的全量行数（分页器用）。"""

    total: int
    items: List[ViewerListItem]


class DailyDanmakuPoint(BaseModel):
    """按天弹幕量（本地日期 ``YYYY-MM-DD``）。"""

    day: str
    count: int


class ViewerInsightsResponse(BaseModel):
    """互动分析聚合：活跃分桶互斥（today/week/month/older）。"""

    total_viewers: int
    active_today: int
    active_week: int
    active_month: int
    active_older: int
    never_replied: int
    gift_viewers: int
    daily_danmaku: List[DailyDanmakuPoint]


class ViewerDetailResponse(BaseModel):
    """单观众档案：viewers 统计行 + 明细三表聚合。"""

    user_id: str
    user_name: str
    message_count: int
    gift_count: int
    replied_count: int
    interaction_count: int
    last_active_ms: int
    first_seen_ms: Optional[int] = Field(default=None, description="明细三表中最早出现时刻；无明细为 NULL")
    gift_total_count: int = Field(default=0, description="礼物总件数（SUM(gift_count)）")
    sc_total_amount: float = Field(default=0.0, description="SC 总金额（元）")
    sc_total_count: int = Field(default=0, description="SC 条数")
    session_count: int = Field(default=0, description="参与过的直播场次数")


class DialogueItem(BaseModel):
    """对话交织行：观众消息（viewer）或主播对其的回复（reply）。"""

    id: int = Field(..., description="live_chat 行主键，前端跨批去重用")
    kind: str = Field(..., description="viewer=观众消息 / reply=主播回复")
    content: str
    timestamp_ms: int
    live_session_id: Optional[int] = None
    message_type: Optional[str] = Field(default=None, description="viewer 行的消息类型（danmaku/guard/enter）")
    message_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    simulated: bool = False


class DialogueResponse(BaseModel):
    """对话批次响应：``next_before`` 为下一批游标（观众消息主轴），无更多为 NULL。"""

    items: List[DialogueItem]
    next_before: Optional[int] = None


class GiftItem(BaseModel):
    """礼物明细行。"""

    timestamp_ms: int
    live_session_id: Optional[int] = None
    gift_name: str
    gift_count: int
    simulated: bool = False


class SuperChatItem(BaseModel):
    """SC 明细行。"""

    timestamp_ms: int
    live_session_id: Optional[int] = None
    amount: float
    message: str
    simulated: bool = False


class ContributionsResponse(BaseModel):
    """观众贡献：汇总数字 + 礼物与 SC 明细（时间倒序，最新在前）。"""

    gift_total_count: int
    sc_total_amount: float
    sc_total_count: int
    gifts: List[GiftItem]
    super_chats: List[SuperChatItem]


class ViewerSessionItem(BaseModel):
    """观众参与的单个场次聚合行。"""

    live_session_id: int
    title: Optional[str] = None
    message_count: int
    first_ms: int
    last_ms: int


class ViewerSessionsResponse(BaseModel):
    """参与场次列表（最近参与在前）。"""

    items: List[ViewerSessionItem]


def _row_to_list_item(row: Any) -> ViewerListItem:
    return ViewerListItem(
        user_id=str(row["user_id"]),
        user_name=str(row["user_name"] or ""),
        message_count=int(row["message_count"] or 0),
        gift_count=int(row["gift_count"] or 0),
        replied_count=int(row["replied_count"] or 0),
        interaction_count=int(row["interaction_count"] or 0),
        last_active_ms=int(row["last_active_ms"] or 0),
    )


@router.get("", response_model=ViewerListResponse)
async def list_viewers(
    server: ServerDep,
    search: Annotated[Optional[str], Query(description="user_id / 昵称模糊搜索")] = None,
    order_by: str = "message_count",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ViewerListResponse:
    """列出观众（搜索 / 排序 / 分页）。

    ``order_by`` 合法性由存储层白名单兜底，非法值转 400。
    """
    viewer_repo = _require_viewer_repo(server)
    try:
        rows, total = await viewer_repo.list_viewer_stats(
            search=(search or "").strip() or None,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ViewerListResponse(total=total, items=[_row_to_list_item(row) for row in rows])


@router.get("/insights", response_model=ViewerInsightsResponse)
async def viewer_insights(
    server: ServerDep,
    days: Annotated[int, Query(ge=1, le=90, description="按天弹幕量回看天数")] = 30,
) -> ViewerInsightsResponse:
    """互动分析聚合：观众活跃分桶、回复覆盖与按天弹幕量。"""
    viewer_repo = _require_viewer_repo(server)
    chat_repo = _require_chat_repo(server)
    buckets = await viewer_repo.viewer_insight_buckets()
    daily = await chat_repo.daily_danmaku_counts(days=days)
    return ViewerInsightsResponse(
        total_viewers=int(buckets.get("total", 0)),
        active_today=int(buckets.get("active_today", 0)),
        active_week=int(buckets.get("active_week", 0)),
        active_month=int(buckets.get("active_month", 0)),
        active_older=int(buckets.get("active_older", 0)),
        never_replied=int(buckets.get("never_replied", 0)),
        gift_viewers=int(buckets.get("gift_viewers", 0)),
        daily_danmaku=[DailyDanmakuPoint(day=str(row["day"]), count=int(row["count"])) for row in daily],
    )


@router.get("/{user_id}", response_model=ViewerDetailResponse)
async def get_viewer(user_id: str, server: ServerDep) -> ViewerDetailResponse:
    """单观众档案：统计行 + 活跃边界 + 贡献汇总 + 参与场次数。"""
    viewer_repo = _require_viewer_repo(server)
    chat_repo = _require_chat_repo(server)
    row = await viewer_repo.get_viewer_stats(user_id=user_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"观众不存在: {user_id}")
    bounds = await chat_repo.get_user_activity_bounds(user_id=user_id)
    summary = await chat_repo.summarize_user_contributions(user_id=user_id)
    sessions = await chat_repo.list_user_sessions(user_id=user_id)
    return ViewerDetailResponse(
        user_id=str(row["user_id"]),
        user_name=str(row["user_name"] or ""),
        message_count=int(row["message_count"] or 0),
        gift_count=int(row["gift_count"] or 0),
        replied_count=int(row["replied_count"] or 0),
        interaction_count=int(row["interaction_count"] or 0),
        last_active_ms=int(row["last_active_ms"] or 0),
        first_seen_ms=bounds[0] if bounds else None,
        gift_total_count=int(summary.get("gift_total_count", 0)),
        sc_total_amount=float(summary.get("sc_total_amount", 0.0)),
        sc_total_count=int(summary.get("sc_total_count", 0)),
        session_count=len(sessions),
    )


@router.get("/{user_id}/messages", response_model=DialogueResponse)
async def viewer_dialogue(
    user_id: str,
    server: ServerDep,
    before_timestamp_ms: Annotated[Optional[int], Query(description="游标：取早于该时刻的批次")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> DialogueResponse:
    """对话批次：观众消息与主播对其的回复按时间正序交织。

    分页以观众消息为主轴——批次内 viewer 行数不足 ``limit`` 即视为翻尽，
    ``next_before`` 置 NULL。
    """
    chat_repo = _require_chat_repo(server)
    rows = await chat_repo.list_user_dialogue(
        user_id=user_id,
        before_timestamp_ms=before_timestamp_ms,
        limit=limit,
    )
    items: List[DialogueItem] = []
    viewer_ts: List[int] = []
    for row in rows:
        is_reply = str(row["sender_role"]) == "assistant"
        if not is_reply:
            viewer_ts.append(int(row["timestamp_ms"]))
        items.append(
            DialogueItem(
                id=int(row["id"]),
                kind="reply" if is_reply else "viewer",
                content=str(row["content"] or ""),
                timestamp_ms=int(row["timestamp_ms"]),
                live_session_id=int(row["live_session_id"]) if row["live_session_id"] is not None else None,
                message_type=str(row["message_type"]) if not is_reply else None,
                message_id=str(row["message_id"]) if row["message_id"] else None,
                reply_to_message_id=str(row["reply_to_message_id"]) if row["reply_to_message_id"] else None,
                simulated=bool(row["simulated"]),
            )
        )
    next_before = min(viewer_ts) if len(viewer_ts) >= limit else None
    return DialogueResponse(items=items, next_before=next_before)


@router.get("/{user_id}/contributions", response_model=ContributionsResponse)
async def viewer_contributions(
    user_id: str,
    server: ServerDep,
    limit: Annotated[int, Query(ge=1, le=100, description="礼物 / SC 明细各自最多条数")] = 100,
) -> ContributionsResponse:
    """观众贡献：礼物与 SC 明细（时间倒序）+ 汇总数字。"""
    chat_repo = _require_chat_repo(server)
    summary = await chat_repo.summarize_user_contributions(user_id=user_id)
    gift_rows = await chat_repo.list_user_gifts(user_id=user_id, limit=limit)
    sc_rows = await chat_repo.list_user_super_chats(user_id=user_id, limit=limit)
    return ContributionsResponse(
        gift_total_count=int(summary.get("gift_total_count", 0)),
        sc_total_amount=float(summary.get("sc_total_amount", 0.0)),
        sc_total_count=int(summary.get("sc_total_count", 0)),
        gifts=[
            GiftItem(
                timestamp_ms=int(row["timestamp_ms"]),
                live_session_id=int(row["live_session_id"]) if row["live_session_id"] is not None else None,
                gift_name=str(row["gift_name"] or ""),
                gift_count=int(row["gift_count"] or 0),
                simulated=bool(row["simulated"]),
            )
            for row in gift_rows
        ],
        super_chats=[
            SuperChatItem(
                timestamp_ms=int(row["timestamp_ms"]),
                live_session_id=int(row["live_session_id"]) if row["live_session_id"] is not None else None,
                amount=float(row["amount"] or 0.0),
                message=str(row["message"] or ""),
                simulated=bool(row["simulated"]),
            )
            for row in sc_rows
        ],
    )


@router.get("/{user_id}/sessions", response_model=ViewerSessionsResponse)
async def viewer_sessions(user_id: str, server: ServerDep) -> ViewerSessionsResponse:
    """观众参与过的场次（按场次聚合发言数与时间范围，最近参与在前）。"""
    chat_repo = _require_chat_repo(server)
    rows = await chat_repo.list_user_sessions(user_id=user_id)
    return ViewerSessionsResponse(
        items=[
            ViewerSessionItem(
                live_session_id=int(row["live_session_id"]),
                title=str(row["title"]) if row["title"] else None,
                message_count=int(row["message_count"] or 0),
                first_ms=int(row["first_ms"]),
                last_ms=int(row["last_ms"]),
            )
            for row in rows
        ]
    )
