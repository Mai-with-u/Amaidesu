"""编排页提问箱 / 话题卡 API Schema。

话题卡本体复用存储层 ``TalkCard`` / ``NewTalkCard`` / ``TalkCardUpdate`` 契约，
这里只包一层 ``{success, message}`` 响应外壳与批量导入、重置的请求体。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from src.modules.storage.models.talk_card import NewTalkCard, TalkCard

__all__ = [
    "TalkCardImportRequest",
    "TalkCardListResponse",
    "TalkCardMutateResponse",
    "TalkCardResetRequest",
]

# 单次批量导入上限：防止误贴一整本文档把卡池撑爆
TALK_CARD_IMPORT_MAX = 200


class TalkCardListResponse(BaseModel):
    """卡池列表响应（存储未就绪时 success=false、cards 为空）。"""

    success: bool
    message: str = ""
    cards: List[TalkCard] = Field(default_factory=list)


class TalkCardMutateResponse(BaseModel):
    """写操作统一响应：新建 / 导入返回落库后的卡，改卡返回改后的卡，删除与重置只带计数。"""

    success: bool
    message: str = ""
    cards: List[TalkCard] = Field(default_factory=list)
    affected: int = 0


class TalkCardImportRequest(BaseModel):
    """批量导入请求体：一次塞入多张卡。"""

    cards: List[NewTalkCard] = Field(..., min_length=1, max_length=TALK_CARD_IMPORT_MAX)


class TalkCardResetRequest(BaseModel):
    """重置已用状态请求体：不给 ids 即重置全部卡（彩排后一键复原）。"""

    ids: Optional[List[int]] = None
