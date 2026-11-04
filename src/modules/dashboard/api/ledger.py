"""记忆账本端点：承诺、场次档案与整理状态（WebUI 记忆页的数据接口）

下播整理把每个正式场整理成承诺账本与场次档案，本模块只提供查看与人工纠错：
标了结、判误记、删除、手建、删档案行、把失败场次排进重试。

- GET    /memory/commitments                  承诺列表（按状态 / 对象 / 游戏筛选）
- POST   /memory/commitments                  手建一条承诺
- POST   /memory/commitments/{id}/close       标已了结（做完或放弃）
- POST   /memory/commitments/{id}/void        判误记（留墓碑，之后不再重复登记）
- DELETE /memory/commitments/{id}             彻底删除
- GET    /memory/archive/{session_id}         某一场的档案行
- DELETE /memory/archive/lines/{line_id}      删一行档案
- GET    /memory/settle                       整理状态（最近的正式场与待整理数）
- POST   /memory/settle/{session_id}/resettle 重新排进整理队列（清整理完成时刻与重试次数）

账本是"她说过什么"的证据面：原话片段由整理时逐字校验过，删掉台词行也不影响阅读。
本模块不做 SQL，读写全部经仓储；未注入仓储时端点返回 503。
"""

from typing import TYPE_CHECKING, Annotated, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.modules.dashboard.dependencies import server_component
from src.modules.logging import get_logger
from src.modules.dashboard.schemas.ledger import (
    ArchiveLineItem,
    ArchiveListResponse,
    CommitmentCloseRequest,
    CommitmentCreateRequest,
    CommitmentItem,
    CommitmentListResponse,
    LedgerMutationResponse,
    SettleListResponse,
    SettleSessionItem,
)
from src.modules.storage.models.memory import Commitment, SettleReport
from src.modules.storage.repos.sessions import MAX_SETTLE_ATTEMPTS
from src.modules.types.session_kind import SESSION_KIND_LIVE

if TYPE_CHECKING:
    from src.modules.storage.repos import ArchiveRepo, CommitmentRepo, SessionRepo, ViewerRepo

logger = get_logger("MemoryLedgerApi")

router = APIRouter()

# 组件访问声明：账本仓储未装配（极简启动 / 部分测试）时统一 503
CommitmentRepoDep = Annotated[
    "CommitmentRepo", Depends(server_component("commitments_repo", missing_detail="承诺账本不可用"))
]
ArchiveRepoDep = Annotated["ArchiveRepo", Depends(server_component("archive_repo", missing_detail="场次档案不可用"))]
SessionRepoDep = Annotated["SessionRepo", Depends(server_component("session_repo", missing_detail="场次表不可用"))]
# 观众昵称是展示用的加项：没有观众仓储时列表照常返回，只是不显示昵称
ViewerRepoDep = Annotated[Optional["ViewerRepo"], Depends(server_component("viewer_repo", required=False))]

# 列表筛选用：一次最多回多少条承诺
_MAX_LIST_LIMIT = 500
# 整理状态页展示最近几场正式场
_SETTLE_LIST_LIMIT = 20


def _to_item(commitment: Commitment, viewer_name: Optional[str]) -> CommitmentItem:
    """承诺行转响应行；对具体观众的那几条带上观众当前昵称。"""
    return CommitmentItem(
        id=commitment.id,
        what=commitment.what,
        quote=commitment.quote,
        viewer_id=commitment.viewer_id,
        viewer_name=viewer_name,
        game=commitment.game,
        save_id=commitment.save_id,
        save_name=commitment.save_name,
        status=commitment.status,
        created_session_id=commitment.created_session_id,
        close_quote=commitment.close_quote,
        created_at_ms=commitment.created_at_ms,
        updated_at_ms=commitment.updated_at_ms,
    )


async def _viewer_names(repo: Optional["ViewerRepo"], viewer_ids: List[int]) -> Dict[int, str]:
    """批量取观众当前昵称（同一个观众只查一次）；取不到就跳过。"""
    names: Dict[int, str] = {}
    if repo is None:
        return names
    for viewer_id in dict.fromkeys(viewer_ids):
        try:
            viewer = await repo.get_viewer(viewer_id=int(viewer_id))
        except Exception as exc:  # noqa: BLE001 - 昵称只是展示加项，读失败不影响列表
            logger.debug(f"取观众 {viewer_id} 昵称失败，列表不带昵称: {exc}", exc=exc)
            continue
        if viewer is not None and viewer.user_name:
            names[int(viewer_id)] = str(viewer.user_name)
    return names


def _status_filter(raw: Optional[str]) -> Optional[List[str]]:
    """状态筛选：open / closed / void；all 或空不过滤；别的一律当参数错。"""
    if raw is None or not raw.strip() or raw.strip().lower() == "all":
        return None
    value = raw.strip().lower()
    if value not in ("open", "closed", "void"):
        raise HTTPException(status_code=422, detail=f"未知的承诺状态: {raw}")
    return [value]


@router.get("/commitments", response_model=CommitmentListResponse)
async def list_commitments(
    commitments: CommitmentRepoDep,
    viewers: ViewerRepoDep,  # noqa: B008 - FastAPI 依赖注入模式
    status: Annotated[Optional[str], Query(description="open / closed / void；空或 all 不过滤")] = None,
    viewer_id: Annotated[Optional[int], Query(description="只看对某位观众的承诺")] = None,
    game: Annotated[Optional[str], Query(description="只看挂在某个游戏上的承诺")] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIST_LIMIT, description="最多返回条数")] = 200,
    offset: Annotated[int, Query(ge=0, description="跳过的条数")] = 0,
) -> CommitmentListResponse:
    """承诺列表（新的在前）与计数；对具体观众的带观众当前昵称。"""
    statuses = _status_filter(status)
    rows = await commitments.list_commitments(
        statuses=statuses, viewer_id=viewer_id, game=game, limit=limit, offset=offset
    )
    names = await _viewer_names(viewers, [row.viewer_id for row in rows if row.viewer_id is not None])
    total = await commitments.count_commitments(statuses=statuses)
    return CommitmentListResponse(
        items=[_to_item(row, names.get(int(row.viewer_id)) if row.viewer_id is not None else None) for row in rows],
        total=total,
        open_count=await commitments.count_commitments(statuses=["open"]),
        void_count=await commitments.count_commitments(statuses=["void"]),
    )


@router.post("/commitments", response_model=CommitmentItem)
async def create_commitment(
    request: CommitmentCreateRequest,
    commitments: CommitmentRepoDep,
    viewers: ViewerRepoDep,  # noqa: B008 - FastAPI 依赖注入模式
) -> CommitmentItem:
    """运营者手建一条承诺（旧笔记里还想兑现的事，切换前搬过来）。"""
    commitment_id = await commitments.insert_commitment(
        what=request.what,
        quote=request.quote,
        viewer_id=request.viewer_id,
        game=request.game,
    )
    if commitment_id is None:
        raise HTTPException(status_code=409, detail="这条承诺没写进去（标题为空或原话重复）")
    created = await commitments.get_commitment(commitment_id=commitment_id)
    if created is None:
        raise HTTPException(status_code=500, detail="承诺已写入但读不回来")
    names = await _viewer_names(viewers, [created.viewer_id] if created.viewer_id is not None else [])
    return _to_item(created, names.get(int(created.viewer_id)) if created.viewer_id is not None else None)


@router.post("/commitments/{commitment_id}/close", response_model=LedgerMutationResponse)
async def close_commitment(
    commitment_id: int,
    commitments: CommitmentRepoDep,
    request: Optional[CommitmentCloseRequest] = None,
) -> LedgerMutationResponse:
    """标已了结（做完或放弃都算）；已了结或判误记的不再改。"""
    close_quote = (request.close_quote if request is not None else "").strip() or None
    changed = await commitments.close_commitment(commitment_id=commitment_id, close_quote=close_quote)
    if not changed:
        raise HTTPException(status_code=409, detail="这条承诺已经了结、被判误记或不存在")
    return LedgerMutationResponse(success=True, detail="已标为完成")


@router.post("/commitments/{commitment_id}/void", response_model=LedgerMutationResponse)
async def void_commitment(commitment_id: int, commitments: CommitmentRepoDep) -> LedgerMutationResponse:
    """判误记：留一行墓碑，之后整理再遇到同一件事不会重新登记。"""
    changed = await commitments.void_commitment(commitment_id=commitment_id)
    if not changed:
        raise HTTPException(status_code=404, detail=f"承诺不存在: {commitment_id}")
    return LedgerMutationResponse(success=True, detail="已判为误记")


@router.delete("/commitments/{commitment_id}", response_model=LedgerMutationResponse)
async def delete_commitment(commitment_id: int, commitments: CommitmentRepoDep) -> LedgerMutationResponse:
    """彻底删除一条承诺（连墓碑也不留）。"""
    changed = await commitments.delete_commitment(commitment_id=commitment_id)
    if not changed:
        raise HTTPException(status_code=404, detail=f"承诺不存在: {commitment_id}")
    return LedgerMutationResponse(success=True, detail="已删除")


@router.get("/archive/{live_session_id}", response_model=ArchiveListResponse)
async def list_archive_lines(live_session_id: int, archive: ArchiveRepoDep) -> ArchiveListResponse:
    """某一场的档案行（写完不改，运营者可以删单行）。"""
    rows = await archive.list_lines(live_session_id=int(live_session_id))
    return ArchiveListResponse(
        live_session_id=int(live_session_id),
        items=[
            ArchiveLineItem(
                id=row.id,
                live_session_id=row.live_session_id,
                text=row.text,
                quote=row.quote,
                evidence_chat_id=row.evidence_chat_id,
                game=row.game,
                save_id=row.save_id,
                save_name=row.save_name,
            )
            for row in rows
        ],
    )


@router.delete("/archive/lines/{line_id}", response_model=LedgerMutationResponse)
async def delete_archive_line(line_id: int, archive: ArchiveRepoDep) -> LedgerMutationResponse:
    """删一行档案；场次复盘页与记忆页共用。"""
    changed = await archive.delete_line(line_id=int(line_id))
    if not changed:
        raise HTTPException(status_code=404, detail=f"档案行不存在: {line_id}")
    return LedgerMutationResponse(success=True, detail="已删除这一行")


def _parse_report(raw: Optional[str]) -> Optional[SettleReport]:
    """整理报告是 JSON 文本：读不懂就当没有报告（页面少显示一块，不影响其余字段）。"""
    if not raw:
        return None
    try:
        return SettleReport.model_validate_json(raw)
    except Exception as exc:  # noqa: BLE001 - 历史数据或半写状态都可能读不懂，降级即可
        logger.warning(f"整理报告读不懂（按无报告展示）: {exc}", exc=exc)
        return None


@router.get("/settle", response_model=SettleListResponse)
async def list_settle_state(sessions: SessionRepoDep, archive: ArchiveRepoDep) -> SettleListResponse:
    """最近正式场的整理状态与待整理场数；失败原因与未登记清单在这里给页面看。"""
    pending_count = await sessions.count_sessions_pending_settle(max_attempts=MAX_SETTLE_ATTEMPTS)
    rows = await sessions.list_live_sessions(kind=SESSION_KIND_LIVE, limit=_SETTLE_LIST_LIMIT)
    items: List[SettleSessionItem] = []
    for row in rows:
        if row.ended_at_ms is None:
            continue
        lines = await archive.list_lines(live_session_id=int(row.id))
        items.append(
            SettleSessionItem(
                live_session_id=int(row.id),
                title=row.title,
                started_at_ms=int(row.started_at_ms),
                ended_at_ms=int(row.ended_at_ms),
                settled_at_ms=row.settled_at_ms,
                settle_attempts=int(row.settle_attempts),
                settle_report=_parse_report(row.settle_report),
                archive_count=len(lines),
            )
        )
    return SettleListResponse(pending_count=pending_count, max_attempts=MAX_SETTLE_ATTEMPTS, items=items)


@router.post("/settle/{live_session_id}/resettle", response_model=LedgerMutationResponse)
async def resettle_session(live_session_id: int, sessions: SessionRepoDep) -> LedgerMutationResponse:
    """把一场重新排进整理队列：清掉整理完成时刻与重试次数（已整理过的场次也会真重跑）。

    失败场次与整理完但想重做的场次共用这一个出口；重跑时整场替换，不会写出重复行。
    不存在的场次返回 404；彩排场从不整理、进行中的场次还没内容可整理，都返回 409。
    """
    session = await sessions.get_live_session(live_session_id=int(live_session_id))
    if session is None:
        raise HTTPException(status_code=404, detail=f"场次不存在: {live_session_id}")
    if session.kind != SESSION_KIND_LIVE:
        raise HTTPException(status_code=409, detail="彩排场不整理，不能重新整理")
    if session.ended_at_ms is None:
        raise HTTPException(status_code=409, detail="场次还没结束，不能重新整理")
    changed = await sessions.requeue_settlement(live_session_id=int(live_session_id))
    if not changed:
        raise HTTPException(status_code=409, detail="场次状态已变化，没能排进整理队列")
    return LedgerMutationResponse(success=True, detail="已排进下一次整理")


__all__ = ["router"]
