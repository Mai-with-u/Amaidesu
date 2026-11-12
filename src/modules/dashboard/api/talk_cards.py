"""编排页提问箱 / 话题卡 CRUD API（``/api/v1/talk-cards*``）。

运营在编排台维护主播杂谈冷场时可抽的自备话题卡（提问箱 / 往场趣事 / 二选一态度）：

- ``GET    /api/v1/talk-cards``             — 卡池列表（可按 kind 过滤）
- ``POST   /api/v1/talk-cards``             — 新建一张卡（直播中随时塞，下一窗她就能抽到）
- ``POST   /api/v1/talk-cards/import``      — 批量导入
- ``PATCH  /api/v1/talk-cards/{id}``        — 部分修改（含启用 / 停用）
- ``DELETE /api/v1/talk-cards/{id}``        — 删除
- ``POST   /api/v1/talk-cards/reset-used``  — 清空已用标记（彩排后复原）

存储读写走组合根注入的 ``DashboardServer.talk_card_repo``；未注入时一律
``success=false`` 降级。错误约定同流程单库：不抛 HTTPException，``{success, message}`` 包络。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Optional

from fastapi import APIRouter, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.talk_cards import (
    TalkCardImportRequest,
    TalkCardListResponse,
    TalkCardMutateResponse,
    TalkCardResetRequest,
)
from src.modules.logging import get_logger
from src.modules.storage.models.talk_card import TALK_CARD_KINDS, NewTalkCard, TalkCardUpdate

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer


router = APIRouter()
logger = get_logger("TalkCardsAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]

_REPO_MISSING = "存储未就绪，话题卡不可用"


def _repo(server: "DashboardServer") -> Optional[Any]:
    return getattr(server, "talk_card_repo", None)


@router.get("", response_model=TalkCardListResponse)
async def list_talk_cards(server: ServerDep, kind: Optional[str] = None) -> TalkCardListResponse:
    """卡池列表：编排台抽屉展示全部卡（含停用与已用状态）。"""
    repo = _repo(server)
    if repo is None:
        return TalkCardListResponse(success=False, message=_REPO_MISSING)
    if kind is not None and kind not in TALK_CARD_KINDS:
        return TalkCardListResponse(success=False, message=f"未知卡种：{kind}")
    try:
        cards = await repo.list_cards(kind=kind)
    except Exception as exc:
        logger.exception(f"读取话题卡列表失败: {exc}")
        return TalkCardListResponse(success=False, message=f"读取失败: {exc}")
    return TalkCardListResponse(success=True, cards=cards)


@router.post("", response_model=TalkCardMutateResponse)
async def create_talk_card(card: NewTalkCard, server: ServerDep) -> TalkCardMutateResponse:
    """新建一张卡：导演直播中随时塞一条新问题，主播下一个冷场窗就能抽到。"""
    repo = _repo(server)
    if repo is None:
        return TalkCardMutateResponse(success=False, message=_REPO_MISSING)
    try:
        created = await repo.add_cards([card])
    except Exception as exc:
        logger.exception(f"新建话题卡失败: {exc}")
        return TalkCardMutateResponse(success=False, message=f"新建失败: {exc}")
    return TalkCardMutateResponse(success=True, message="已加入话题匣", cards=created, affected=len(created))


@router.post("/import", response_model=TalkCardMutateResponse)
async def import_talk_cards(request: TalkCardImportRequest, server: ServerDep) -> TalkCardMutateResponse:
    """批量导入一组卡（同一事务写入，要么全进要么全不进）。"""
    repo = _repo(server)
    if repo is None:
        return TalkCardMutateResponse(success=False, message=_REPO_MISSING)
    try:
        created = await repo.add_cards(request.cards)
    except Exception as exc:
        logger.exception(f"批量导入话题卡失败: {exc}")
        return TalkCardMutateResponse(success=False, message=f"导入失败: {exc}")
    return TalkCardMutateResponse(
        success=True, message=f"已导入 {len(created)} 张", cards=created, affected=len(created)
    )


@router.patch("/{card_id}", response_model=TalkCardMutateResponse)
async def update_talk_card(card_id: int, patch: TalkCardUpdate, server: ServerDep) -> TalkCardMutateResponse:
    """部分修改一张卡（只改给出的字段）。"""
    repo = _repo(server)
    if repo is None:
        return TalkCardMutateResponse(success=False, message=_REPO_MISSING)
    try:
        updated = await repo.update_card(card_id, patch)
    except Exception as exc:
        logger.exception(f"修改话题卡失败: id={card_id} {exc}")
        return TalkCardMutateResponse(success=False, message=f"修改失败: {exc}")
    if updated is None:
        return TalkCardMutateResponse(success=False, message="话题卡不存在")
    return TalkCardMutateResponse(success=True, message="已保存", cards=[updated], affected=1)


@router.delete("/{card_id}", response_model=TalkCardMutateResponse)
async def delete_talk_card(card_id: int, server: ServerDep) -> TalkCardMutateResponse:
    """删除一张卡。"""
    repo = _repo(server)
    if repo is None:
        return TalkCardMutateResponse(success=False, message=_REPO_MISSING)
    try:
        deleted = await repo.delete_card(card_id)
    except Exception as exc:
        logger.exception(f"删除话题卡失败: id={card_id} {exc}")
        return TalkCardMutateResponse(success=False, message=f"删除失败: {exc}")
    if not deleted:
        return TalkCardMutateResponse(success=False, message="话题卡不存在")
    return TalkCardMutateResponse(success=True, message="已删除", affected=1)


@router.post("/reset-used", response_model=TalkCardMutateResponse)
async def reset_used_talk_cards(request: TalkCardResetRequest, server: ServerDep) -> TalkCardMutateResponse:
    """清空已用标记：不给 ids 即全部复原（彩排抽过的卡正式场还能再用）。"""
    repo = _repo(server)
    if repo is None:
        return TalkCardMutateResponse(success=False, message=_REPO_MISSING)
    try:
        affected = await repo.reset_used(card_ids=request.ids)
    except Exception as exc:
        logger.exception(f"重置话题卡已用状态失败: {exc}")
        return TalkCardMutateResponse(success=False, message=f"重置失败: {exc}")
    return TalkCardMutateResponse(success=True, message=f"已复原 {affected} 张", affected=affected)
