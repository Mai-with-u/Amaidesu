"""记忆页"游戏里的人"的请求与响应模型：玩家列表、观众对号确认与删玩家。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class LinkedViewer(BaseModel):
    """运营者确认过和这位玩家是同一个人的直播间观众（只用来连带删除，从不告诉她）。"""

    viewer_id: int = Field(description="观众主键")
    user_name: str = Field(default="", description="观众当前昵称；读不到为空")
    confirmed_at_ms: int = Field(description="确认的时刻（Unix 毫秒）")


class PlayerItem(BaseModel):
    """玩家列表的一行：她在游戏里遇到过的一位玩家。"""

    id: int = Field(description="玩家主键")
    game: str = Field(description="哪个游戏")
    uuid: str = Field(description="游戏给这个账号的 UUID（删玩家按它进不记录名单）")
    name: str = Field(description="最近一次见到时的名字")
    former_names: List[str] = Field(default_factory=list, description="以前用过的名字")
    first_seen_ms: int = Field(description="第一次见到的时刻")
    last_seen_ms: int = Field(description="最近一次见到的时刻")
    interactions: Dict[str, int] = Field(
        default_factory=dict, description="互动条数（按种类：greet 打招呼的场数 / gift / spar / together / helped）"
    )
    linked_viewers: List[LinkedViewer] = Field(default_factory=list, description="确认过的关联观众")
    forget_requested_at_ms: Optional[int] = Field(
        default=None, description="他在公屏发'忘了我'的时刻；宽限期满后自动删除"
    )


class PlayerListResponse(BaseModel):
    """玩家列表（最近见到的在前）。"""

    items: List[PlayerItem] = Field(default_factory=list, description="玩家")


class PlayerLinkRequest(BaseModel):
    """确认这位玩家是直播间的哪位观众。"""

    viewer_id: int = Field(description="观众主键")


class PlayerMutationResponse(BaseModel):
    """改动类操作的回执。"""

    success: bool = Field(description="是否成功")
    detail: str = Field(default="", description="给运营者看的一句说明")
