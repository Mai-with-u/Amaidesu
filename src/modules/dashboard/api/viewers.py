"""观众数据端点

观众域的 WebUI 消费面：``viewers`` 观众表 + 明细四表（live_chat / gifts /
super_chats / guards）按内部观众 ID（``viewer_id``）的聚合查询，以及观众身份
的人工处理（待确认、合并、取消关联）。装配逻辑在
``services.viewer_profiles``，本模块只做 HTTP 协议转换。

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

金额口径：存储层为平台最小虚拟货币单位（B 站金瓜子），展示层 ÷1000 = 元。
数据现实：上舰（guard）进 guards 明细表；进房 / 关注 / 点赞不落库——
页面如实呈现这些边界，不臆造数据。
"""

from typing import TYPE_CHECKING, Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.viewers import (
    ContributionsResponse,
    DialogueResponse,
    IdentityActionResponse,
    MergeRequest,
    PendingViewersResponse,
    UnlinkRequest,
    ViewerDetailResponse,
    ViewerInsightsResponse,
    ViewerListResponse,
    ViewerSessionsResponse,
)
from src.modules.dashboard.services.viewer_profiles import (
    RepoUnavailableError,
    build_contributions,
    build_dialogue,
    build_insights,
    build_pending_viewers,
    build_viewer_detail,
    build_viewer_sessions,
    list_viewers,
    require_chat_repo,
    require_identity_repo,
    require_viewer_repo,
)
from src.modules.storage.repos.viewer_identity import ViewerIdentityError

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()

# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


def _available(repo_of: Any, server: "DashboardServer") -> Any:
    """取仓储，未装配（领域异常）映射为 503。"""
    try:
        return repo_of(server)
    except RepoUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("", response_model=ViewerListResponse)
async def list_viewers_endpoint(
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
    try:
        return await list_viewers(
            _available(require_viewer_repo, server),
            platform=platform,
            search=search,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/insights", response_model=ViewerInsightsResponse)
async def viewer_insights(
    server: ServerDep,
    days: Annotated[int, Query(ge=1, le=90, description="按天弹幕量回看天数")] = 30,
) -> ViewerInsightsResponse:
    """互动分析聚合：观众活跃分桶、回复覆盖与按天弹幕量。"""
    return await build_insights(
        _available(require_viewer_repo, server), _available(require_chat_repo, server), days=days
    )


@router.get("/pending", response_model=PendingViewersResponse)
async def list_pending_viewers(
    server: ServerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PendingViewersResponse:
    """待确认观众：与老观众同名但超出关联时限或候选不唯一，等人工判断是不是同一个人。"""
    return await build_pending_viewers(
        _available(require_identity_repo, server),
        _available(require_viewer_repo, server),
        limit=limit,
        offset=offset,
    )


@router.get("/{viewer_id}", response_model=ViewerDetailResponse)
async def get_viewer(viewer_id: int, server: ServerDep) -> ViewerDetailResponse:
    """单观众档案：观众行 + 外部 ID + 用过的昵称 + 活跃边界 + 贡献汇总 + 参与场次数。"""
    detail = await build_viewer_detail(
        _available(require_viewer_repo, server), _available(require_chat_repo, server), viewer_id
    )
    if detail is None:
        raise HTTPException(status_code=404, detail=f"观众不存在: #{viewer_id}")
    return detail


@router.get("/{viewer_id}/messages", response_model=DialogueResponse)
async def viewer_dialogue(
    viewer_id: int,
    server: ServerDep,
    before_timestamp_ms: Annotated[Optional[int], Query(description="游标：取早于该时刻的批次")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> DialogueResponse:
    """对话批次：观众消息与主播对其的回复按时间正序交织（游标分页）。"""
    return await build_dialogue(
        _available(require_chat_repo, server),
        viewer_id,
        before_timestamp_ms=before_timestamp_ms,
        limit=limit,
    )


@router.get("/{viewer_id}/contributions", response_model=ContributionsResponse)
async def viewer_contributions(
    viewer_id: int,
    server: ServerDep,
    limit: Annotated[int, Query(ge=1, le=100, description="礼物 / SC 明细各自最多条数")] = 100,
) -> ContributionsResponse:
    """观众贡献：礼物与 SC 明细（时间倒序）+ 汇总数字。"""
    return await build_contributions(_available(require_chat_repo, server), viewer_id, limit=limit)


@router.get("/{viewer_id}/sessions", response_model=ViewerSessionsResponse)
async def viewer_sessions(viewer_id: int, server: ServerDep) -> ViewerSessionsResponse:
    """观众参与过的场次（按场次聚合发言数与时间范围，最近参与在前）。"""
    return await build_viewer_sessions(_available(require_chat_repo, server), viewer_id)


@router.post("/{viewer_id}/merge", response_model=IdentityActionResponse)
async def merge_viewer(viewer_id: int, request: MergeRequest, server: ServerDep) -> IdentityActionResponse:
    """把这位观众并入 ``target_viewer_id``（待确认页的"同意""改选"与档案页的手动合并都走这里）。"""
    identity_repo = _available(require_identity_repo, server)
    try:
        await identity_repo.merge(source_id=viewer_id, target_id=request.target_viewer_id)
    except ViewerIdentityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return IdentityActionResponse(viewer_id=request.target_viewer_id)


@router.post("/{viewer_id}/keep", response_model=IdentityActionResponse)
async def keep_viewer_separate(viewer_id: int, server: ServerDep) -> IdentityActionResponse:
    """保持新建：确认这位待确认观众与建议的老观众不是同一个人。"""
    identity_repo = _available(require_identity_repo, server)
    if not await identity_repo.keep_separate(viewer_id=viewer_id):
        raise HTTPException(status_code=409, detail=f"观众 #{viewer_id} 不存在或不在待确认状态")
    return IdentityActionResponse(viewer_id=viewer_id)


@router.post("/{viewer_id}/unlink", response_model=IdentityActionResponse)
async def unlink_viewer_id(viewer_id: int, request: UnlinkRequest, server: ServerDep) -> IdentityActionResponse:
    """取消关联：把这位观众身上的一种外部 ID 拆成新观众，返回新观众 ID。"""
    identity_repo = _available(require_identity_repo, server)
    try:
        new_id = await identity_repo.unlink(viewer_id=viewer_id, id_kind=request.id_kind)
    except ViewerIdentityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return IdentityActionResponse(viewer_id=new_id)
