"""记忆账本端点：承诺、场次档案与整理状态（WebUI 记忆页的数据接口）

下播整理把每个正式场整理成承诺账本与场次档案，本模块只提供查看与人工纠错：
标了结、判误记、删除、手建、删档案行、把失败场次排进重试。

- GET    /memory/commitments                  承诺列表（按状态 / 对象 / 游戏筛选）
- POST   /memory/commitments                  手建一条承诺
- POST   /memory/commitments/{id}/close       标已了结（做完或放弃）
- POST   /memory/commitments/{id}/void        判误记（留墓碑，之后不再重复登记）
- DELETE /memory/commitments/{id}             彻底删除
- GET    /memory/archive/{session_id}         某一场的档案行（连同审核状态、每行在不在最新快照里）
- POST   /memory/archive/{session_id}/lines   从这一场她的台词里截原话新增一行（锁住、放进最新快照）
- PUT    /memory/archive/lines/{line_id}/title    改标题（锁住）
- PUT    /memory/archive/lines/{line_id}/lasting  标或取消稳定的事（锁住）
- POST   /memory/archive/lines/{line_id}/pin      放回最新一份快照（一份快照至多 4 行）
- POST   /memory/archive/lines/{line_id}/unpin    取消放回（改回普通行，还在快照里）
- DELETE /memory/archive/lines/{line_id}      删一行档案
- GET    /memory/settle                       整理状态（最近的正式场、待整理数与待审数）
- POST   /memory/settle/{session_id}/reviewed 标已审
- POST   /memory/settle/{session_id}/resettle 重新排进整理队列（清整理完成时刻与重试次数）
- GET    /memory/candidates                   观众候选（待审 / 已批 / 已驳回）与待审数
- POST   /memory/candidates/{id}/approve      批准（观众定下的写成档案行，她说欠着的登记成承诺）
- POST   /memory/candidates/{id}/reject       驳回（同一条以后不再列）

人改过的行之后的整理不能去掉或替换，人放回、新增的行整理不挤掉；每次改动都记进改动记录。
观众候选批准前对她完全不可见；弹幕原文里的已知观众昵称按回想工具的口径遮成"某位观众"。

账本是"她说过什么"的证据面：原话片段由整理时逐字校验过，删掉台词行也不影响阅读。
本模块不做 SQL，读写全部经仓储；未注入仓储时端点返回 503。
"""

import re
from typing import TYPE_CHECKING, Annotated, Dict, List, NoReturn, Optional, Sequence, Set

from fastapi import APIRouter, Depends, HTTPException, Query

from src.modules.dashboard.dependencies import server_component
from src.modules.logging import get_logger
from src.modules.dashboard.schemas.ledger import (
    ArchiveLastingRequest,
    ArchiveLineCreateRequest,
    ArchiveLineItem,
    ArchiveListResponse,
    ArchiveTitleRequest,
    CandidateApproveRequest,
    CandidateItem,
    CandidateListResponse,
    CommitmentCloseRequest,
    CommitmentCreateRequest,
    CommitmentItem,
    CommitmentListResponse,
    LedgerMutationResponse,
    NearCommitment,
    SettleListResponse,
    SettleSessionItem,
)
from src.modules.storage.models.memory import Commitment, SettleReport, ViewerCandidate
from src.modules.storage.privacy import mask_names_in_text
from src.modules.storage.repos.memory_review import MAX_PINNED_LINES, MemoryReviewError
from src.modules.storage.repos.sessions import MAX_SETTLE_ATTEMPTS
from src.modules.types.session_kind import SESSION_KIND_LIVE
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.storage.repos import (
        ArchiveRepo,
        CommitmentRepo,
        MemoryReviewRepo,
        SessionRepo,
        ViewerCandidateRepo,
        ViewerRepo,
    )

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
# 记忆页人工审核：改动端点缺了它 503；档案列表缺了它只是不带审核状态
ReviewRepoDep = Annotated[
    "MemoryReviewRepo", Depends(server_component("memory_review_repo", missing_detail="记忆审核不可用"))
]
OptionalReviewRepoDep = Annotated[
    Optional["MemoryReviewRepo"], Depends(server_component("memory_review_repo", required=False))
]
CandidateRepoDep = Annotated[
    "ViewerCandidateRepo", Depends(server_component("viewer_candidate_repo", missing_detail="观众候选不可用"))
]
# 候选列表遮名的称呼：和回想工具跨场遮名同一个说法
_MASKED_VIEWER = "某位观众"

# 列表筛选用：一次最多回多少条承诺
_MAX_LIST_LIMIT = 500
# "她说欠着的"候选旁至多列几条账上最接近的承诺；比对前去掉的空白与标点
_NEAREST_LIMIT = 3
# 至少重合几个相邻两字才算沾边：只重合"大家""上次"这类一个常用词的不列
_NEAREST_MIN_OVERLAP = 2
_NOT_WORD = re.compile(r"[\s\W_]+", re.UNICODE)
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


def _raise_review_error(exc: MemoryReviewError) -> NoReturn:
    """审核操作做不了的原因转成状态码：不存在 404、状态不允许 409、填得不对 422。"""
    status = {"not_found": 404, "conflict": 409, "invalid": 422}.get(exc.kind, 409)
    raise HTTPException(status_code=status, detail=exc.message) from exc


@router.get("/archive/{live_session_id}", response_model=ArchiveListResponse)
async def list_archive_lines(
    live_session_id: int, archive: ArchiveRepoDep, review: OptionalReviewRepoDep
) -> ArchiveListResponse:
    """某一场的档案行，带上每行是不是人改过、在不在最新一份快照里，以及这一场审没审。"""
    rows = await archive.list_lines(live_session_id=int(live_session_id))
    state = await review.session_state(live_session_id=int(live_session_id)) if review is not None else None
    snapshot = state.snapshot_lines if state is not None else {}
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
                human_locked=row.human_locked,
                in_snapshot=int(row.id) in snapshot,
                lasting=snapshot[int(row.id)].lasting if int(row.id) in snapshot else False,
                pinned=snapshot[int(row.id)].pinned if int(row.id) in snapshot else False,
            )
            for row in rows
        ],
        reviewed_at_ms=state.reviewed_at_ms if state is not None else None,
        settled=state is not None and state.settled_at_ms is not None,
        snapshot_session_id=state.snapshot_session_id if state is not None else None,
        snapshot_pinned_count=state.pinned_count if state is not None else 0,
        snapshot_pinned_max=MAX_PINNED_LINES,
    )


@router.post("/archive/{live_session_id}/lines", response_model=LedgerMutationResponse)
async def create_archive_line(
    live_session_id: int, request: ArchiveLineCreateRequest, review: ReviewRepoDep
) -> LedgerMutationResponse:
    """整理漏写的事：从这一场她播出的台词里截一段逐字原话、写标题，新增一行并放进最新快照。"""
    try:
        await review.add_line(
            live_session_id=int(live_session_id),
            evidence_chat_id=int(request.evidence_chat_id),
            quote=request.quote,
            text=request.text,
            lasting=bool(request.lasting),
            at_ms=now_ms(),
        )
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return LedgerMutationResponse(success=True, detail="已新增这一行，下一场开播会记得")


@router.put("/archive/lines/{line_id}/title", response_model=LedgerMutationResponse)
async def edit_archive_title(
    line_id: int, request: ArchiveTitleRequest, review: ReviewRepoDep
) -> LedgerMutationResponse:
    """改一行档案的标题；改过的行之后的整理不能去掉或替换（原话不变）。"""
    try:
        changed = await review.edit_title(line_id=int(line_id), text=request.text, at_ms=now_ms())
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return LedgerMutationResponse(success=changed, detail="已改标题" if changed else "标题没变")


@router.put("/archive/lines/{line_id}/lasting", response_model=LedgerMutationResponse)
async def set_archive_lasting(
    line_id: int, request: ArchiveLastingRequest, review: ReviewRepoDep
) -> LedgerMutationResponse:
    """标或取消最新快照里这一行是稳定的事；之后的整理按人标的算。"""
    try:
        await review.set_lasting(line_id=int(line_id), lasting=bool(request.lasting), at_ms=now_ms())
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return LedgerMutationResponse(success=True, detail="已标为稳定的事" if request.lasting else "已取消稳定的事")


@router.post("/archive/lines/{line_id}/pin", response_model=LedgerMutationResponse)
async def pin_archive_line(line_id: int, review: ReviewRepoDep) -> LedgerMutationResponse:
    """把被挤出或被去掉的一行放回最新一份快照（排最前，整理不挤掉）。"""
    try:
        await review.pin_line(line_id=int(line_id), at_ms=now_ms())
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return LedgerMutationResponse(success=True, detail="已放回快照，下一场开播会记得")


@router.post("/archive/lines/{line_id}/unpin", response_model=LedgerMutationResponse)
async def unpin_archive_line(line_id: int, review: ReviewRepoDep) -> LedgerMutationResponse:
    """取消放回：这一行改回普通行，还在快照里；快照满 12 行时可能被挤出简报（仍在档案里，回想查得到）。"""
    try:
        await review.unpin_line(line_id=int(line_id), at_ms=now_ms())
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return LedgerMutationResponse(success=True, detail="已取消放回")


@router.delete("/archive/lines/{line_id}", response_model=LedgerMutationResponse)
async def delete_archive_line(
    line_id: int, archive: ArchiveRepoDep, review: OptionalReviewRepoDep
) -> LedgerMutationResponse:
    """删一行档案；场次复盘页与记忆页共用。装了审核仓储时连同改动记录一起写。"""
    if review is not None:
        changed = await review.delete_line(line_id=int(line_id), at_ms=now_ms())
    else:
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
                reviewed_at_ms=row.reviewed_at_ms,
            )
        )
    return SettleListResponse(
        pending_count=pending_count,
        max_attempts=MAX_SETTLE_ATTEMPTS,
        review_pending_count=sum(1 for item in items if item.settled_at_ms is not None and item.reviewed_at_ms is None),
        items=items,
    )


@router.post("/settle/{live_session_id}/reviewed", response_model=LedgerMutationResponse)
async def mark_session_reviewed(live_session_id: int, review: ReviewRepoDep) -> LedgerMutationResponse:
    """把一场标成已审（只是状态，不改记忆）；还没整理完的场次不能标。"""
    try:
        await review.mark_reviewed(live_session_id=int(live_session_id), at_ms=now_ms())
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return LedgerMutationResponse(success=True, detail="已标为已审")


@router.post("/settle/{live_session_id}/resettle", response_model=LedgerMutationResponse)
async def resettle_session(
    live_session_id: int, sessions: SessionRepoDep, review: OptionalReviewRepoDep
) -> LedgerMutationResponse:
    """把一场重新排进整理队列：清掉整理完成时刻与重试次数（已整理过的场次也会真重跑）。

    失败场次与整理完但想重做的场次共用这一个出口；重跑时整场替换，不会写出重复行。
    不存在的场次返回 404；彩排场从不整理、进行中的场次还没内容可整理，都返回 409。
    这一场有人改过的行、或它的快照里有人放回的行时也返回 409：整场替换会把人工改动冲掉。
    人批准的观众来源行不拦：重新整理不删它们，整理器把它们接着带进重写的快照。
    """
    session = await sessions.get_live_session(live_session_id=int(live_session_id))
    if session is None:
        raise HTTPException(status_code=404, detail=f"场次不存在: {live_session_id}")
    if session.kind != SESSION_KIND_LIVE:
        raise HTTPException(status_code=409, detail="彩排场不整理，不能重新整理")
    if session.ended_at_ms is None:
        raise HTTPException(status_code=409, detail="场次还没结束，不能重新整理")
    if review is not None and await review.count_human_edits(live_session_id=int(live_session_id)) > 0:
        raise HTTPException(status_code=409, detail="这一场有人改过或放回的行，重新整理会整场替换、把人工改动冲掉")
    changed = await sessions.requeue_settlement(live_session_id=int(live_session_id))
    if not changed:
        raise HTTPException(status_code=409, detail="场次状态已变化，没能排进整理队列")
    return LedgerMutationResponse(success=True, detail="已排进下一次整理")


def _bigrams(text: str) -> Set[str]:
    """字面相近的比对单位：去掉空白与标点后的相邻两字（单字文本取它自己）。"""
    plain = _NOT_WORD.sub("", text or "").lower()
    return {plain[index : index + 2] for index in range(len(plain) - 1)} or ({plain} if plain else set())


def _nearest_commitments(text: str, rows: Sequence[Commitment]) -> List[NearCommitment]:
    """账上和这句话字面最接近的几条承诺（相邻两字的重合度，从高到低，至多 3 条，不沾边的不列）。

    只是给审的人看的参照：她说"上次答应过唱那首歌"，旁边摆着账上的"给大家唱歌"，就看得出是
    漏登记还是同一件事换了说法；一条都不沾，多半是她顺着观众的话编的。
    """
    wanted = _bigrams(text)
    scored = []
    for row in rows:
        have = _bigrams(f"{row.what} {row.quote}")
        overlap = len(wanted & have)
        if overlap >= _NEAREST_MIN_OVERLAP:
            scored.append((overlap / max(1, len(wanted | have)), -int(row.id), row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [NearCommitment(id=row.id, what=row.what, status=row.status) for _score, _id, row in scored[:_NEAREST_LIMIT]]


def _candidate_item(row: ViewerCandidate, names: List[str], ledger: Sequence[Commitment] = ()) -> CandidateItem:
    """候选转响应行：弹幕原文里的已知观众昵称遮成"某位观众"；待审的"她说欠着的"附上账上最接近的几条。"""
    nearest: List[NearCommitment] = []
    if row.kind == "claimed_missing" and row.status == "pending":
        nearest = _nearest_commitments(f"{row.title} {row.reply_quote}", ledger)
    return CandidateItem(
        id=row.id,
        live_session_id=row.live_session_id,
        kind=row.kind,
        title=row.title,
        viewer_quote=mask_names_in_text(row.viewer_quote, names, replacement=_MASKED_VIEWER),
        reply_quote=row.reply_quote,
        game=row.game,
        save_name=row.save_name,
        status=row.status,
        created_at_ms=row.created_at_ms,
        decided_at_ms=row.decided_at_ms,
        archive_line_id=row.archive_line_id,
        commitment_id=row.commitment_id,
        nearest_commitments=nearest,
    )


async def _ledger_for_claims(rows: Sequence[ViewerCandidate], commitments: "CommitmentRepo") -> List[Commitment]:
    """有待审的"她说欠着的"时取账上的未了结与已了结承诺（判误记的不算"账上有"）；读失败只少参照。"""
    if not any(row.kind == "claimed_missing" and row.status == "pending" for row in rows):
        return []
    try:
        return await commitments.list_commitments(statuses=["open", "closed"], limit=_MAX_LIST_LIMIT)
    except Exception as exc:  # noqa: BLE001 - 参照读不到时候选照常列
        logger.warning(f"读承诺账给「她说欠着的」候选作参照失败: {exc}", exc=exc)
        return []


async def _known_names(viewers: Optional["ViewerRepo"]) -> List[str]:
    """已知观众昵称（遮名用）；没有观众仓储或读失败时不遮——候选原文本来就只截了定下那件事的一段。"""
    if viewers is None:
        return []
    try:
        return await viewers.list_known_names()
    except Exception as exc:  # noqa: BLE001 - 遮名表读不到时列表照常返回
        logger.warning(f"读已知观众昵称失败，候选原文不遮名: {exc}", exc=exc)
        return []


@router.get("/candidates", response_model=CandidateListResponse)
async def list_candidates(
    candidates: CandidateRepoDep,
    viewers: ViewerRepoDep,  # noqa: B008 - FastAPI 依赖注入模式
    commitments: CommitmentRepoDep,
    status: Annotated[str, Query(description="pending / approved / rejected / all")] = "pending",
) -> CandidateListResponse:
    """观众候选列表（新的在前）与待审数；待审的"她说欠着的"附上账上最接近的几条承诺。"""
    value = status.strip().lower()
    if value not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(status_code=422, detail=f"未知的候选状态: {status}")
    rows = await candidates.list_candidates(status=None if value == "all" else value)
    names = await _known_names(viewers)
    ledger = await _ledger_for_claims(rows, commitments)
    return CandidateListResponse(
        items=[_candidate_item(row, names, ledger) for row in rows], pending_count=await candidates.count_pending()
    )


@router.post("/candidates/{candidate_id}/approve", response_model=CandidateItem)
async def approve_candidate(
    candidate_id: int,
    candidates: CandidateRepoDep,
    viewers: ViewerRepoDep,  # noqa: B008 - FastAPI 依赖注入模式
    request: Optional[CandidateApproveRequest] = None,
) -> CandidateItem:
    """批准：观众定下的写成一行观众来源的档案行并放进最新快照，她说欠着的登记成未了结承诺。"""
    body = request or CandidateApproveRequest()
    title = body.title if body.title is not None and body.title.strip() else None
    try:
        row = await candidates.approve(
            candidate_id=int(candidate_id), title=title, lasting=body.lasting, at_ms=now_ms()
        )
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return _candidate_item(row, await _known_names(viewers))


@router.post("/candidates/{candidate_id}/reject", response_model=LedgerMutationResponse)
async def reject_candidate(candidate_id: int, candidates: CandidateRepoDep) -> LedgerMutationResponse:
    """驳回：留着这一条，同一条弹幕或同样的标题以后不再列。"""
    try:
        await candidates.reject(candidate_id=int(candidate_id), at_ms=now_ms())
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return LedgerMutationResponse(success=True, detail="已驳回，以后不再列这一条")


__all__ = ["router"]
