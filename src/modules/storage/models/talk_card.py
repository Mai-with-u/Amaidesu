"""话题卡（talk_cards 表）数据契约。

话题卡是主播在杂谈环节没人发弹幕时可以拿来开话头的"自备素材"，由运营在编排台
预先塞入或直播中临时追加，三类：

- ``marshmallow``：提问箱（运营或观众提前投递的问题，``asker`` 可写提问人称呼）
- ``episode``：往场趣事（之前直播里真发生过的事，供她自曝糗事）
- ``opinion``：二选一态度话题（她可以公开自问自答表态）

卡片跨场次长期存在；``used_in_session_id`` 记录"最近一次在哪场被用过"，同场不再
自动预摆，换场后自然又可用（运营也可在编排台一键重置）。时间字段统一毫秒。
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["TALK_CARD_KINDS", "NewTalkCard", "TalkCard", "TalkCardKind", "TalkCardUpdate"]

TalkCardKind = Literal["marshmallow", "episode", "opinion"]

# 合法卡种（存储 CHECK 约束、API 校验与工具参数枚举共用这一份）
TALK_CARD_KINDS: tuple[str, ...] = ("marshmallow", "episode", "opinion")


def _clean_tags(value: object) -> List[str]:
    """标签去空白、去空串、保序去重；None 视为无标签。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("tags 必须是字符串列表")
    seen: List[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


class NewTalkCard(BaseModel):
    """新建话题卡的入参（编排台手建、批量导入、外部点播临时卡共用）。"""

    model_config = ConfigDict(extra="forbid")

    kind: TalkCardKind = Field(..., description="卡种：marshmallow 提问箱 / episode 往场趣事 / opinion 二选一态度")
    title: str = Field(..., min_length=1, max_length=120, description="一句话标题（提问原文或话头）")
    body: str = Field(default="", max_length=1000, description="补充说明（趣事经过、二选一的两个选项等）")
    asker: str = Field(default="", max_length=40, description="提问人称呼（可空，如匿名观众）")
    tags: List[str] = Field(default_factory=list, description="标签（运营分类用）")
    enabled: bool = Field(default=True, description="停用的卡不会被预摆或抽到")

    @field_validator("title", "body", "asker", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> List[str]:
        return _clean_tags(value)


class TalkCardUpdate(BaseModel):
    """编排台改卡的部分更新入参：只改显式给出的字段。"""

    model_config = ConfigDict(extra="forbid")

    kind: Optional[TalkCardKind] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    body: Optional[str] = Field(default=None, max_length=1000)
    asker: Optional[str] = Field(default=None, max_length=40)
    tags: Optional[List[str]] = None
    enabled: Optional[bool] = None

    @field_validator("title", "body", "asker", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> Optional[List[str]]:
        return None if value is None else _clean_tags(value)


class TalkCard(NewTalkCard):
    """落库后的话题卡完整行。"""

    id: int = Field(..., description="卡片主键")
    used_in_session_id: Optional[int] = Field(default=None, description="最近一次被用的场次 id（未用过为空）")
    used_at_ms: Optional[int] = Field(default=None, description="最近一次被用的时刻（Unix epoch 毫秒）")
    created_at_ms: int = Field(..., description="创建时刻（Unix epoch 毫秒）")
    updated_at_ms: int = Field(..., description="最后修改时刻（Unix epoch 毫秒）")

    def is_used_in(self, session_id: Optional[int]) -> bool:
        """本场是否已用过：场次未知（未开播调试）时按"有使用记录即算用过"处理，避免同一张卡反复被摆出来。"""
        if self.used_at_ms is None:
            return False
        return session_id is None or self.used_in_session_id == session_id
