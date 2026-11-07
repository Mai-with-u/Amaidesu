"""游戏里的人：记忆页的玩家列表、观众对号确认与删玩家。

- GET    /memory/players                         玩家列表（最近见到的在前）：名字、曾用名、互动条数、关联观众
- POST   /memory/players/{id}/links              确认"这位玩家是直播间的哪位观众"
- DELETE /memory/players/{id}/links/{viewer_id}  取消关联
- DELETE /memory/players/{id}                    删玩家
- GET    /memory/players/candidates              玩家自述列表（按状态筛）与待审数
- POST   /memory/players/candidates/{id}/approve 批准（可改标题）：只进这位玩家的玩家卡
- POST   /memory/players/candidates/{id}/reject  驳回：同一句他的话、同样的标题以后不再列

对号只由运营者确认，同名、名字像、同时出现都不自动关联；关联只用来连带删除（删观众、"忘了我"时
一起删这位玩家）和给运营者看清楚，从不告诉她。删玩家时他的互动、公屏聊天、对他的承诺随之删除，
名字换成"一位玩家"，UUID 进不记录名单，之后他再出现也不再记。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Dict, List, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.modules.dashboard.dependencies import server_component
from src.modules.dashboard.schemas.players import (
    LinkedViewer,
    PlayerCandidateApproveRequest,
    PlayerCandidateItem,
    PlayerCandidateListResponse,
    PlayerItem,
    PlayerLinkRequest,
    PlayerListResponse,
    PlayerMutationResponse,
    PlayerSaveItem,
)
from src.modules.logging import get_logger
from src.modules.storage.models.game_players import PlayerCandidate
from src.modules.storage.repos.memory_review import MemoryReviewError
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.storage.privacy import PrivacyService
    from src.modules.storage.repos import GamePlayerRepo, ViewerRepo

logger = get_logger("MemoryPlayersApi")

router = APIRouter()

# 组件访问声明：玩家仓储或隐私服务未装配（极简启动 / 部分测试）时 503
PlayerRepoDep = Annotated[
    "GamePlayerRepo", Depends(server_component("game_player_repo", missing_detail="游戏玩家记录不可用"))
]
PrivacyDep = Annotated["PrivacyService", Depends(server_component("privacy_service", missing_detail="隐私服务不可用"))]
# 关联观众的昵称是展示用的加项：没有观众仓储时只列编号
ViewerRepoDep = Annotated[Optional["ViewerRepo"], Depends(server_component("viewer_repo", required=False))]

# 列表一次至多回多少位玩家
_MAX_PLAYERS = 500


async def _viewer_name(viewers: Optional["ViewerRepo"], viewer_id: int) -> str:
    """观众当前昵称；读不到为空串（列表照常返回）。"""
    if viewers is None:
        return ""
    try:
        viewer = await viewers.get_viewer(viewer_id=viewer_id)
    except Exception as exc:  # noqa: BLE001 - 昵称只是展示加项
        logger.warning(f"读观众 {viewer_id} 的昵称失败: {exc}", exc=exc)
        return ""
    return viewer.user_name if viewer is not None else ""


@router.get("", response_model=PlayerListResponse)
async def list_players(
    players: PlayerRepoDep,
    viewers: ViewerRepoDep,  # noqa: B008 - FastAPI 依赖注入模式
    game: Annotated[Optional[str], Query(description="只看某个游戏")] = None,
) -> PlayerListResponse:
    """玩家列表：最近见到的在前，带曾用名、见过他的存档（含服务器验不验账号）、互动条数与确认过的关联观众。"""
    rows = await players.list_players(game=game, limit=_MAX_PLAYERS)
    counts = await players.interaction_counts(player_ids=[row.id for row in rows])
    names: Dict[int, str] = {}
    items: List[PlayerItem] = []
    for row in rows:
        linked: List[LinkedViewer] = []
        for link in await players.list_links(player_id=row.id):
            if link.viewer_id not in names:
                names[link.viewer_id] = await _viewer_name(viewers, link.viewer_id)
            linked.append(
                LinkedViewer(
                    viewer_id=link.viewer_id, user_name=names[link.viewer_id], confirmed_at_ms=link.confirmed_at_ms
                )
            )
        items.append(
            PlayerItem(
                id=row.id,
                game=row.game,
                uuid=row.uuid,
                name=row.name,
                former_names=row.former_names(),
                # 见过他的存档连同账号验证：离线模式的存档面板上标明认人只认得名字
                saves=[
                    PlayerSaveItem(save_id=save.save_id, save_name=save.save_name, online_mode=save.online_mode)
                    for save in row.saves
                ],
                first_seen_ms=row.first_seen_ms,
                last_seen_ms=row.last_seen_ms,
                interactions=counts.get(row.id, {}),
                linked_viewers=linked,
                forget_requested_at_ms=row.forget_requested_at_ms,
            )
        )
    return PlayerListResponse(items=items)


async def _candidate_item(players: "GamePlayerRepo", row: PlayerCandidate) -> PlayerCandidateItem:
    """自述转响应行：带上这位玩家现在的名字（审核的人要知道是谁说的）。"""
    player = await players.get_player(player_id=row.player_id)
    return PlayerCandidateItem(
        id=row.id,
        live_session_id=row.live_session_id,
        player_id=row.player_id,
        player_name=player.name if player is not None else "",
        title=row.title,
        player_quote=row.player_quote,
        reply_quote=row.reply_quote,
        reply_in="game_chat" if row.reply_game_chat_id is not None else "speech",
        status=row.status,
        created_at_ms=row.created_at_ms,
        decided_at_ms=row.decided_at_ms,
    )


def _raise_review_error(exc: MemoryReviewError) -> NoReturn:
    """审核做不了的原因转成状态码：不存在 404、已经审过 409、标题填得不对 422。"""
    status = {"not_found": 404, "conflict": 409, "invalid": 422}.get(exc.kind, 409)
    raise HTTPException(status_code=status, detail=exc.message) from exc


@router.get("/candidates", response_model=PlayerCandidateListResponse)
async def list_player_candidates(
    players: PlayerRepoDep,
    status: Annotated[str, Query(description="pending / approved / rejected / all")] = "pending",
) -> PlayerCandidateListResponse:
    """玩家自述列表（新的在前）与待审数。"""
    value = status.strip().lower()
    if value not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(status_code=422, detail=f"未知的自述状态: {status}")
    rows = await players.list_player_candidates(status=None if value == "all" else value)
    return PlayerCandidateListResponse(
        items=[await _candidate_item(players, row) for row in rows],
        pending_count=await players.count_pending_player_candidates(),
    )


@router.post("/candidates/{candidate_id}/approve", response_model=PlayerCandidateItem)
async def approve_player_candidate(
    candidate_id: int,
    players: PlayerRepoDep,
    request: Optional[PlayerCandidateApproveRequest] = None,
) -> PlayerCandidateItem:
    """批准：之后他在游戏里找她时，这件事写在他的玩家卡上；不进开播简报。"""
    body = request or PlayerCandidateApproveRequest()
    title = body.title if body.title is not None and body.title.strip() else None
    try:
        row = await players.decide_player_candidate(
            candidate_id=int(candidate_id), approve=True, title=title, at_ms=now_ms()
        )
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return await _candidate_item(players, row)


@router.post("/candidates/{candidate_id}/reject", response_model=PlayerMutationResponse)
async def reject_player_candidate(candidate_id: int, players: PlayerRepoDep) -> PlayerMutationResponse:
    """驳回：留着这一条，同一句他的话、同样的标题以后不再列。"""
    try:
        await players.decide_player_candidate(candidate_id=int(candidate_id), approve=False, at_ms=now_ms())
    except MemoryReviewError as exc:
        _raise_review_error(exc)
    return PlayerMutationResponse(success=True, detail="已驳回，以后不再列这一条")


@router.post("/{player_id}/links", response_model=PlayerMutationResponse)
async def link_viewer(
    player_id: int,
    request: PlayerLinkRequest,
    players: PlayerRepoDep,
    viewers: ViewerRepoDep,  # noqa: B008 - FastAPI 依赖注入模式
) -> PlayerMutationResponse:
    """运营者确认这位玩家就是直播间的这位观众：之后删观众、"忘了我"会连带删他。"""
    if await players.get_player(player_id=player_id) is None:
        raise HTTPException(status_code=404, detail=f"玩家不存在: #{player_id}")
    if viewers is not None and await viewers.get_viewer(viewer_id=request.viewer_id) is None:
        raise HTTPException(status_code=404, detail=f"观众不存在: #{request.viewer_id}")
    await players.link_viewer(viewer_id=request.viewer_id, player_id=player_id, at_ms=now_ms())
    return PlayerMutationResponse(success=True, detail="已确认关联：删这位观众时会连带删这位玩家")


@router.delete("/{player_id}/links/{viewer_id}", response_model=PlayerMutationResponse)
async def unlink_viewer(player_id: int, viewer_id: int, players: PlayerRepoDep) -> PlayerMutationResponse:
    """取消关联。"""
    if not await players.unlink_viewer(viewer_id=viewer_id, player_id=player_id):
        raise HTTPException(status_code=404, detail="这两位之间没有确认过的关联")
    return PlayerMutationResponse(success=True, detail="已取消关联")


@router.delete("/{player_id}", response_model=PlayerMutationResponse)
async def delete_player(player_id: int, privacy: PrivacyDep) -> PlayerMutationResponse:
    """删玩家：互动、公屏聊天、对他的承诺随之删除，名字换成"一位玩家"，UUID 进不记录名单。"""
    if not await privacy.delete_player(player_id=player_id, add_to_forget_list=True):
        raise HTTPException(status_code=404, detail=f"玩家不存在: #{player_id}")
    return PlayerMutationResponse(success=True, detail="已删除，他再出现也不会被记下")


__all__ = ["router"]
