"""观众数据端点

观众域的 WebUI 消费面：``viewers`` 观众表 + 明细四表（live_chat / gifts /
super_chats / guards）按内部观众 ID（``viewer_id``）的聚合查询，以及观众身份
的人工处理（待确认、合并、取消关联）。

- ``GET /viewers``                         观众列表（平台过滤 / 搜索 / 排序 / 分页）
- ``GET /viewers/insights``                互动分析聚合（活跃分桶 / 回复覆盖 / 按天弹幕量）
- ``GET /viewers/pending``                 待确认观众及其建议并入的老观众
- ``GET /viewers/{viewer_id}``             单观众档案（统计 + 外部 ID + 用过的昵称 + 贡献汇总）
- ``GET /viewers/{viewer_id}/messages``    对话批次（观众消息与主播回复交织，游标分页）
- ``GET /viewers/{viewer_id}/contributions`` 礼物与 SC 明细 + 汇总
- ``GET /viewers/{viewer_id}/sessions``    参与场次聚合
- ``POST /viewers/{viewer_id}/merge``      把这位观众并入另一位
- ``POST /viewers/{viewer_id}/keep``       保持新建（清除待确认标记）
- ``POST /viewers/{viewer_id}/unlink``     把一个外部 ID 拆成新观众

同一位观众可能有账号 ID 与开放平台 ID 两种外部 ID，单观众端点一律按内部观众 ID
定位；列表行的 ``user_id`` 是代表外部 ID（账号 ID 优先），只作展示。

金额口径：存储层为平台最小虚拟货币单位（B 站金瓜子），本层展示 ÷1000 = 元。
数据现实：上舰（guard）进 guards 明细表；进房 / 关注 / 点赞不落库——
页面如实呈现这些边界，不臆造数据。
"""

from typing import TYPE_CHECKING, Annotated, Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.storage.repos.viewer_identity import ViewerIdentityError
from src.modules.types.currency import to_cny

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


def _require_identity_repo(server: "DashboardServer") -> Any:
    """取观众身份人工处理仓储，未装配时 503。"""
    repo = getattr(server, "viewer_identity_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="观众身份仓储不可用")
    return repo


def _require_chat_repo(server: "DashboardServer") -> Any:
    """取直播明细仓储，未装配时 503。"""
    repo = getattr(server, "chat_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="直播明细仓储不可用")
    return repo


class ViewerListItem(BaseModel):
    """单行观众统计（viewers 表行的投影）。"""

    viewer_id: int = Field(description="内部观众 ID（单观众端点按它定位）")
    platform: str
    user_id: str = Field(description="代表外部 ID（账号 ID 优先，其次开放平台 ID；没有真实 ID 时为空）")
    user_name: str
    link_status: str = Field(default="confirmed", description="confirmed=已确认 / pending=待人工确认")
    message_count: int
    gift_count: int
    replied_count: int
    interaction_count: int
    paid_count: int
    paid_amount: int = Field(description="付费总额（金瓜子，展示层 ÷1000 = 元）")
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


class ViewerNameItem(BaseModel):
    """观众用过的一个昵称及其最早、最晚出现时刻。"""

    user_name: str
    first_seen_ms: int
    last_seen_ms: int


class ViewerBrief(BaseModel):
    """另一位观众的简要信息（待确认的并入建议等）。"""

    viewer_id: int
    user_name: str


class ViewerDetailResponse(BaseModel):
    """单观众档案：viewers 观众行 + 用过的昵称 + 明细表聚合。"""

    viewer_id: int
    platform: str
    user_id: str = Field(description="代表外部 ID（账号 ID 优先）")
    account_id: Optional[str] = Field(default=None, description="平台账号 ID（B 站 uid）")
    open_id: Optional[str] = Field(default=None, description="开放平台 ID（B 站 open_id）")
    open_app_id: Optional[str] = Field(default=None, description="发出 open_id 的开放平台应用；空串 = 存量数据不明")
    user_name: str
    names: List[ViewerNameItem] = Field(default_factory=list, description="用过的昵称（含当前昵称），最近在前")
    link_status: str = Field(default="confirmed", description="confirmed=已确认 / pending=待人工确认")
    link_candidate: Optional[ViewerBrief] = Field(default=None, description="待确认时建议并入的老观众")
    message_count: int
    gift_count: int
    replied_count: int
    interaction_count: int
    paid_count: int
    paid_amount: int = Field(default=0, description="付费总额（金瓜子）")
    last_active_ms: int
    first_seen_ms: Optional[int] = Field(default=None, description="明细表中最早出现时刻；无明细为 NULL")
    gift_total_count: int = Field(default=0, description="礼物总件数")
    gift_total_amount: int = Field(default=0, description="礼物标价总额（金瓜子）")
    sc_total_amount: float = Field(default=0.0, description="SC 总金额（元，金瓜子 ÷1000）")
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
    total_price: int = Field(default=0, description="标价总额（金瓜子）")
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


class PendingViewerItem(BaseModel):
    """一位待确认观众：新建的这一位 + 建议并入的老观众，供人工比对。"""

    viewer_id: int
    platform: str
    user_name: str
    account_id: Optional[str] = None
    open_id: Optional[str] = None
    message_count: int
    interaction_count: int
    paid_amount: int = Field(default=0, description="付费总额（金瓜子）")
    last_active_ms: int
    candidate: Optional[ViewerBrief] = None
    candidate_account_id: Optional[str] = None
    candidate_open_id: Optional[str] = None
    candidate_name_seen_ms: int = Field(default=0, description="老观众最近一次以该昵称出现的时刻")
    candidate_message_count: int = 0
    candidate_interaction_count: int = 0
    candidate_paid_amount: int = 0
    candidate_last_active_ms: int = 0
    candidate_names: List[str] = Field(default_factory=list, description="老观众用过的昵称（最近在前）")


class PendingViewersResponse(BaseModel):
    """待确认观众列表（最近活跃在前）。"""

    total: int
    items: List[PendingViewerItem]


class MergeRequest(BaseModel):
    """合并请求：把路径里的观众并入 ``target_viewer_id``。"""

    target_viewer_id: int


class UnlinkRequest(BaseModel):
    """取消关联请求：拆出哪一种外部 ID。"""

    id_kind: Literal["account", "open"]


class IdentityActionResponse(BaseModel):
    """人工处理结果：``viewer_id`` 为处理后应跳转查看的观众（合并的目标 / 拆出的新观众 / 原观众）。"""

    viewer_id: int


def _row_to_list_item(row: Any) -> ViewerListItem:
    return ViewerListItem(
        viewer_id=int(row["id"]),
        platform=str(row["platform"] or ""),
        user_id=str(row["user_id"] or ""),
        user_name=str(row["user_name"] or ""),
        link_status=str(row["link_status"] or "confirmed"),
        message_count=int(row["message_count"] or 0),
        gift_count=int(row["gift_count"] or 0),
        replied_count=int(row["replied_count"] or 0),
        interaction_count=int(row["interaction_count"] or 0),
        paid_count=int(row["paid_count"] or 0),
        paid_amount=int(row["paid_amount"] or 0),
        last_active_ms=int(row["last_active_ms"] or 0),
    )


@router.get("", response_model=ViewerListResponse)
async def list_viewers(
    server: ServerDep,
    search: Annotated[Optional[str], Query(description="外部 ID / 昵称模糊搜索")] = None,
    platform: Annotated[Optional[str], Query(description="平台过滤；不传返回全部平台")] = None,
    order_by: str = "message_count",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ViewerListResponse:
    """列出观众（平台过滤 / 搜索 / 排序 / 分页）。

    ``order_by`` 合法性由存储层白名单兜底，非法值转 400。
    """
    viewer_repo = _require_viewer_repo(server)
    try:
        rows, total = await viewer_repo.list_viewer_stats(
            platform=(platform or "").strip() or None,
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


@router.get("/pending", response_model=PendingViewersResponse)
async def list_pending_viewers(
    server: ServerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PendingViewersResponse:
    """待确认观众：与老观众同名但超出关联时限或候选不唯一，等人工判断是不是同一个人。"""
    identity_repo = _require_identity_repo(server)
    viewer_repo = _require_viewer_repo(server)
    total, rows = await identity_repo.list_pending(limit=limit, offset=offset)
    items: List[PendingViewerItem] = []
    for row in rows:
        candidate_id = row["candidate_id"]
        names = await viewer_repo.list_names(viewer_id=int(candidate_id)) if candidate_id is not None else []
        items.append(
            PendingViewerItem(
                viewer_id=int(row["viewer_id"]),
                platform=str(row["platform"] or ""),
                user_name=str(row["user_name"] or ""),
                account_id=row["account_id"],
                open_id=row["open_id"],
                message_count=int(row["message_count"] or 0),
                interaction_count=int(row["interaction_count"] or 0),
                paid_amount=int(row["paid_amount"] or 0),
                last_active_ms=int(row["last_active_ms"] or 0),
                candidate=(
                    ViewerBrief(viewer_id=int(candidate_id), user_name=str(row["candidate_name"] or ""))
                    if candidate_id is not None
                    else None
                ),
                candidate_account_id=row["candidate_account_id"],
                candidate_open_id=row["candidate_open_id"],
                candidate_name_seen_ms=int(row["candidate_name_seen_ms"] or 0),
                candidate_message_count=int(row["candidate_message_count"] or 0),
                candidate_interaction_count=int(row["candidate_interaction_count"] or 0),
                candidate_paid_amount=int(row["candidate_paid_amount"] or 0),
                candidate_last_active_ms=int(row["candidate_last_active_ms"] or 0),
                candidate_names=[str(name_row["user_name"]) for name_row in names],
            )
        )
    return PendingViewersResponse(total=total, items=items)


@router.get("/{viewer_id}", response_model=ViewerDetailResponse)
async def get_viewer(viewer_id: int, server: ServerDep) -> ViewerDetailResponse:
    """单观众档案：观众行 + 外部 ID + 用过的昵称 + 活跃边界 + 贡献汇总 + 参与场次数。"""
    viewer_repo = _require_viewer_repo(server)
    chat_repo = _require_chat_repo(server)
    row = await viewer_repo.get_viewer(viewer_id=viewer_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"观众不存在: #{viewer_id}")
    names = await viewer_repo.list_names(viewer_id=viewer_id)
    candidate = None
    if row["link_candidate_id"] is not None:
        candidate_row = await viewer_repo.get_viewer(viewer_id=int(row["link_candidate_id"]))
        if candidate_row is not None:
            candidate = ViewerBrief(viewer_id=int(candidate_row["id"]), user_name=str(candidate_row["user_name"]))
    bounds = await chat_repo.get_viewer_activity_bounds(viewer_id=viewer_id)
    summary = await chat_repo.summarize_viewer_contributions(viewer_id=viewer_id)
    sessions = await chat_repo.list_viewer_sessions(viewer_id=viewer_id)
    return ViewerDetailResponse(
        viewer_id=int(row["id"]),
        platform=str(row["platform"] or ""),
        user_id=str(row["user_id"] or ""),
        account_id=row["account_id"],
        open_id=row["open_id"],
        open_app_id=row["open_app_id"],
        user_name=str(row["user_name"] or ""),
        names=[
            ViewerNameItem(
                user_name=str(name_row["user_name"]),
                first_seen_ms=int(name_row["first_seen_ms"]),
                last_seen_ms=int(name_row["last_seen_ms"]),
            )
            for name_row in names
        ],
        link_status=str(row["link_status"] or "confirmed"),
        link_candidate=candidate,
        message_count=int(row["message_count"] or 0),
        gift_count=int(row["gift_count"] or 0),
        replied_count=int(row["replied_count"] or 0),
        interaction_count=int(row["interaction_count"] or 0),
        paid_count=int(row["paid_count"] or 0),
        paid_amount=int(row["paid_amount"] or 0),
        last_active_ms=int(row["last_active_ms"] or 0),
        first_seen_ms=bounds[0] if bounds else None,
        gift_total_count=int(summary.get("gift_total_count", 0)),
        gift_total_amount=int(summary.get("gift_total_amount", 0)),
        sc_total_amount=to_cny(int(summary.get("sc_total_amount", 0))) or 0.0,
        sc_total_count=int(summary.get("sc_total_count", 0)),
        session_count=len(sessions),
    )


@router.get("/{viewer_id}/messages", response_model=DialogueResponse)
async def viewer_dialogue(
    viewer_id: int,
    server: ServerDep,
    before_timestamp_ms: Annotated[Optional[int], Query(description="游标：取早于该时刻的批次")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> DialogueResponse:
    """对话批次：观众消息与主播对其的回复按时间正序交织。

    分页以观众消息为主轴——批次内 viewer 行数不足 ``limit`` 即视为翻尽，
    ``next_before`` 置 NULL。
    """
    chat_repo = _require_chat_repo(server)
    rows = await chat_repo.list_viewer_dialogue(
        viewer_id=viewer_id,
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


@router.get("/{viewer_id}/contributions", response_model=ContributionsResponse)
async def viewer_contributions(
    viewer_id: int,
    server: ServerDep,
    limit: Annotated[int, Query(ge=1, le=100, description="礼物 / SC 明细各自最多条数")] = 100,
) -> ContributionsResponse:
    """观众贡献：礼物与 SC 明细（时间倒序）+ 汇总数字。"""
    chat_repo = _require_chat_repo(server)
    summary = await chat_repo.summarize_viewer_contributions(viewer_id=viewer_id)
    gift_rows = await chat_repo.list_viewer_gifts(viewer_id=viewer_id, limit=limit)
    sc_rows = await chat_repo.list_viewer_super_chats(viewer_id=viewer_id, limit=limit)
    return ContributionsResponse(
        gift_total_count=int(summary.get("gift_total_count", 0)),
        sc_total_amount=to_cny(int(summary.get("sc_total_amount", 0))) or 0.0,
        sc_total_count=int(summary.get("sc_total_count", 0)),
        gifts=[
            GiftItem(
                timestamp_ms=int(row["timestamp_ms"]),
                live_session_id=int(row["live_session_id"]) if row["live_session_id"] is not None else None,
                gift_name=str(row["gift_name"] or ""),
                gift_count=int(row["quantity"] or 0),
                total_price=int(row["total_price"] or 0),
                simulated=bool(row["simulated"]),
            )
            for row in gift_rows
        ],
        super_chats=[
            SuperChatItem(
                timestamp_ms=int(row["timestamp_ms"]),
                live_session_id=int(row["live_session_id"]) if row["live_session_id"] is not None else None,
                amount=int(row["total_price"] or 0) / 1000,
                message=str(row["message"] or ""),
                simulated=bool(row["simulated"]),
            )
            for row in sc_rows
        ],
    )


@router.get("/{viewer_id}/sessions", response_model=ViewerSessionsResponse)
async def viewer_sessions(viewer_id: int, server: ServerDep) -> ViewerSessionsResponse:
    """观众参与过的场次（按场次聚合发言数与时间范围，最近参与在前）。"""
    chat_repo = _require_chat_repo(server)
    rows = await chat_repo.list_viewer_sessions(viewer_id=viewer_id)
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


@router.post("/{viewer_id}/merge", response_model=IdentityActionResponse)
async def merge_viewer(viewer_id: int, request: MergeRequest, server: ServerDep) -> IdentityActionResponse:
    """把这位观众并入 ``target_viewer_id``（待确认页的"同意""改选"与档案页的手动合并都走这里）。"""
    identity_repo = _require_identity_repo(server)
    try:
        await identity_repo.merge(source_id=viewer_id, target_id=request.target_viewer_id)
    except ViewerIdentityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return IdentityActionResponse(viewer_id=request.target_viewer_id)


@router.post("/{viewer_id}/keep", response_model=IdentityActionResponse)
async def keep_viewer_separate(viewer_id: int, server: ServerDep) -> IdentityActionResponse:
    """保持新建：确认这位待确认观众与建议的老观众不是同一个人。"""
    identity_repo = _require_identity_repo(server)
    if not await identity_repo.keep_separate(viewer_id=viewer_id):
        raise HTTPException(status_code=409, detail=f"观众 #{viewer_id} 不存在或不在待确认状态")
    return IdentityActionResponse(viewer_id=viewer_id)


@router.post("/{viewer_id}/unlink", response_model=IdentityActionResponse)
async def unlink_viewer_id(viewer_id: int, request: UnlinkRequest, server: ServerDep) -> IdentityActionResponse:
    """取消关联：把这位观众身上的一种外部 ID 拆成新观众，返回新观众 ID。"""
    identity_repo = _require_identity_repo(server)
    try:
        new_id = await identity_repo.unlink(viewer_id=viewer_id, id_kind=request.id_kind)
    except ViewerIdentityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return IdentityActionResponse(viewer_id=new_id)
