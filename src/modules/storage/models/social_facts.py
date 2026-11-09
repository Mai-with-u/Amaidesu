"""游戏社交事实的数据契约（``game_social_facts`` 表）。

一行 = 一条"当前游戏环境里人与人怎么相处"的可核对事实，与承诺账本同构：
行级状态、证据可核、人工锁。

字段说明
--------
``SocialFact`` —— 库里的一行：六类事实之一（环境形态 / 成员关系 / 共享惯例 /
进行中的事 / 她的角色 / 待弄清），带原文依据与证据行引用。

``NewSocialFact`` —— 待写入的一行：仓储写面与结算写面共用。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EvidenceHit",
    "NewSocialFact",
    "SocialFact",
    "SocialFactKind",
    "SOCIAL_FACT_KINDS",
    "SOCIAL_FACT_STATUSES",
]

# 六类事实（英文小写枚举）：环境形态 / 成员关系 / 共享惯例 / 进行中的事 /
# 她的角色 / 待弄清
SocialFactKind = str

SOCIAL_FACT_KINDS: tuple[str, ...] = (
    "setting",
    "members",
    "sharing",
    "works",
    "her_role",
    "open_question",
)

# 行状态：active 现行（注入）/ superseded 被取代（留档备查，不再注入）
SOCIAL_FACT_STATUSES: tuple[str, ...] = ("active", "superseded")

# 空引文行只允许这两类（归纳类）：其余四类必须有可核对的原文依据
QUOTE_OPTIONAL_KINDS: tuple[str, ...] = ("her_role", "open_question")


class SocialFact(BaseModel):
    """``game_social_facts`` 一行：一条可核对的游戏社交事实。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="行号")
    game: str = Field(..., description="游戏标识")
    save_id: str = Field(default="", description="存档编号；空串 = 该游戏不分环境，整游戏一本")
    save_name: str = Field(default="", description="存档显示名")
    kind: str = Field(..., description="六类之一")
    text: str = Field(..., description="一句事实陈述")
    quote: str = Field(default="", description="依据的原文片段；空串只允许归纳类两行")
    evidence_chat_id: Optional[int] = Field(default=None, description="弹幕出处（→ live_chat）")
    evidence_game_chat_id: Optional[int] = Field(default=None, description="游戏公屏出处（→ game_chat）")
    status: str = Field(default="active", description="active / superseded")
    superseded_by: Optional[int] = Field(default=None, description="取代本行的行号")
    human_locked: int = Field(default=0, description="运营者改过即锁：整理与工具都不得改或取代")
    created_session_id: Optional[int] = Field(default=None, description="登记场次")
    updated_session_id: Optional[int] = Field(default=None, description="最近更新场次")
    created_at_ms: int = Field(..., description="写入时刻（Unix epoch 毫秒）")
    updated_at_ms: int = Field(..., description="最近更新时刻（Unix epoch 毫秒）")


class EvidenceHit(BaseModel):
    """按"本场 + 消息号"查到的一条依据行：弹幕观众行（chat）或公屏非她本人的行（game_chat）。"""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(..., description="chat = 弹幕观众行（live_chat）/ game_chat = 公屏玩家行或占位行")
    row_id: int = Field(..., description="命中行的行号")
    content: str = Field(default="", description="命中行原文；公屏占位行为空串（没有存原话）")
    game: str = Field(default="", description="公屏行所在游戏；弹幕为空串")
    save_id: str = Field(default="", description="公屏行所在存档；弹幕为空串")
    save_name: str = Field(default="", description="公屏行所在存档显示名；弹幕为空串")


class NewSocialFact(BaseModel):
    """待写入的一行游戏社交事实（仓储写面与结算写面共用）。"""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., description="六类之一")
    text: str = Field(..., description="一句事实陈述")
    quote: str = Field(default="", description="依据的原文片段")
    game: str = Field(default="", description="游戏标识")
    save_id: str = Field(default="", description="存档编号；空串 = 整游戏一本")
    save_name: str = Field(default="", description="存档显示名")
    evidence_chat_id: Optional[int] = Field(default=None, description="弹幕出处（→ live_chat）")
    evidence_game_chat_id: Optional[int] = Field(default=None, description="游戏公屏出处（→ game_chat）")
    supersedes_id: Optional[int] = Field(default=None, description="被本行取代的行号")
    human_locked: int = Field(default=0, description="运营者锁：只由运营者写入路径置位")
    created_session_id: Optional[int] = Field(default=None, description="登记场次")
    updated_session_id: Optional[int] = Field(default=None, description="最近更新场次")
