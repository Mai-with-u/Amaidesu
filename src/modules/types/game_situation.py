"""游戏情境：游戏 Agent 报给主播的"角色眼下离哪些记住的地点多远"。

主播走到记住的地点跟前时会想起在那里发生过的事（被动想起的地点触发），所以要知道角色离各个
记住的地点有多远。这份情境只从游戏 Agent 已有的缓存里现算（最近一次看自己读到的位置 + 速览
缓存的地点清单），读它不向游戏发任何请求、不改游戏 Agent 怎么做事。

- 只列和角色同一个维度的地点（别的维度按坐标算的"近"不算数），按水平距离由近到远；
- ``age_ms`` 是那次读到位置距今多久：位置只在速览时更新，读方据此判断这份距离还算不算数。

存档不在这里重复：读方按 ``current_saves`` 拿眼下的存档，两样一起用。
"""

from __future__ import annotations

from typing import Tuple

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["GameSituation", "NearbyPlace"]


class NearbyPlace(BaseModel):
    """一个记住的地点与角色的水平距离（整格）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="地点名（游戏里记地点时起的名字）")
    distance: int = Field(..., ge=0, description="和角色的水平距离（格，四舍五入）")


class GameSituation(BaseModel):
    """角色眼下的情境：同一维度里最近的几个记住的地点，以及位置是多久前读到的。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    places: Tuple[NearbyPlace, ...] = Field(default=(), description="同一维度的记住的地点，由近到远")
    age_ms: int = Field(default=0, ge=0, description="算距离用的角色位置是多久前读到的（毫秒）")
