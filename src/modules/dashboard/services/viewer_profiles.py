"""观众档案装配

观众域只读端点的业务装配：仓储行 → 响应模型的投影、跨仓储编排（单观众
档案串观众表 + 明细表、待确认列表逐行补候选昵称、互动分析聚合两表）。
仓储不可用与非法排序字段以领域异常上抛，HTTP 状态码映射留在 api 层。
"""

from typing import TYPE_CHECKING, Any, List, Optional

from src.modules.dashboard.schemas.viewers import (
    ContributionsResponse,
    DialogueItem,
    DialogueResponse,
    DailyDanmakuPoint,
    GiftItem,
    PendingViewerItem,
    PendingViewersResponse,
    SuperChatItem,
    ViewerBrief,
    ViewerDetailResponse,
    ViewerInsightsResponse,
    ViewerListItem,
    ViewerListResponse,
    ViewerNameItem,
    ViewerSessionItem,
    ViewerSessionsResponse,
)
from src.modules.types.currency import to_cny

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer


class RepoUnavailableError(RuntimeError):
    """所需仓储未装配（极简启动形态），api 层映射为 503。"""


def require_viewer_repo(server: "DashboardServer") -> Any:
    """取观众统计仓储，未装配时抛 :class:`RepoUnavailableError`。"""
    repo = server.viewer_repo
    if repo is None:
        raise RepoUnavailableError("观众统计仓储不可用")
    return repo


def require_identity_repo(server: "DashboardServer") -> Any:
    """取观众身份人工处理仓储，未装配时抛 :class:`RepoUnavailableError`。"""
    repo = server.viewer_identity_repo
    if repo is None:
        raise RepoUnavailableError("观众身份仓储不可用")
    return repo


def require_chat_repo(server: "DashboardServer") -> Any:
    """取直播明细仓储，未装配时抛 :class:`RepoUnavailableError`。"""
    repo = server.chat_repo
    if repo is None:
        raise RepoUnavailableError("直播明细仓储不可用")
    return repo


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


async def list_viewers(
    viewer_repo: Any,
    *,
    platform: Optional[str],
    search: Optional[str],
    limit: int,
    offset: int,
    order_by: str,
) -> ViewerListResponse:
    """观众列表（平台过滤 / 搜索 / 排序 / 分页）；非法排序字段由仓储抛 ValueError。"""
    rows, total = await viewer_repo.list_viewer_stats(
        platform=(platform or "").strip() or None,
        search=(search or "").strip() or None,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )
    return ViewerListResponse(total=total, items=[_row_to_list_item(row) for row in rows])


async def build_insights(viewer_repo: Any, chat_repo: Any, *, days: int) -> ViewerInsightsResponse:
    """互动分析聚合：观众活跃分桶（观众表）+ 按天弹幕量（明细表）。"""
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


async def build_pending_viewers(
    identity_repo: Any, viewer_repo: Any, *, limit: int, offset: int
) -> PendingViewersResponse:
    """待确认观众列表：每行按候选观众补齐用过的昵称（供人工比对）。"""
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


async def build_viewer_detail(viewer_repo: Any, chat_repo: Any, viewer_id: int) -> Optional[ViewerDetailResponse]:
    """单观众档案：观众行 + 外部 ID + 用过的昵称 + 活跃边界 + 贡献汇总 + 参与场次数。

    观众不存在返回 None（api 层映射 404）。
    """
    row = await viewer_repo.get_viewer(viewer_id=viewer_id)
    if row is None:
        return None
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


async def build_dialogue(
    chat_repo: Any, viewer_id: int, *, before_timestamp_ms: Optional[int], limit: int
) -> DialogueResponse:
    """对话批次：观众消息与主播对其的回复按时间正序交织。

    分页以观众消息为主轴——批次内 viewer 行数不足 ``limit`` 即视为翻尽，
    ``next_before`` 置 NULL。
    """
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


async def build_contributions(chat_repo: Any, viewer_id: int, *, limit: int) -> ContributionsResponse:
    """观众贡献：礼物与 SC 明细（时间倒序）+ 汇总数字。"""
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


async def build_viewer_sessions(chat_repo: Any, viewer_id: int) -> ViewerSessionsResponse:
    """观众参与过的场次（按场次聚合发言数与时间范围，最近参与在前）。"""
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
