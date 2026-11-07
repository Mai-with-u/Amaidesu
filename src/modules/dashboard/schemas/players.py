"""记忆页"游戏里的人"的请求与响应模型：玩家列表、观众对号确认、删玩家与玩家自述审核。"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.modules.types.game_save import OnlineMode


class LinkedViewer(BaseModel):
    """运营者确认过和这位玩家是同一个人的直播间观众（只用来连带删除，从不告诉她）。"""

    viewer_id: int = Field(description="观众主键")
    user_name: str = Field(default="", description="观众当前昵称；读不到为空")
    confirmed_at_ms: int = Field(description="确认的时刻（Unix 毫秒）")


class PlayerSaveItem(BaseModel):
    """见过这位玩家的一个存档，连同那台服务器验不验正版账号。"""

    save_id: str = Field(description="存档编号")
    save_name: str = Field(default="", description="存档给人看的名字")
    online_mode: OnlineMode = Field(
        default="unknown", description="on 验账号 / off 不验（按名字认人，改名算新人，冒名认不出）/ unknown 没报"
    )


class PlayerItem(BaseModel):
    """玩家列表的一行：她在游戏里遇到过的一位玩家。"""

    id: int = Field(description="玩家主键")
    game: str = Field(description="哪个游戏")
    uuid: str = Field(description="游戏给这个账号的 UUID（删玩家按它进不记录名单）")
    name: str = Field(description="最近一次见到时的名字")
    former_names: List[str] = Field(default_factory=list, description="以前用过的名字")
    saves: List[PlayerSaveItem] = Field(default_factory=list, description="见过他的存档，按第一次见到的先后")
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


class PlayerCandidateItem(BaseModel):
    """玩家自述的一行：他在公屏说的关于他自己的事、她当场接住了，人批准才进他的玩家卡。"""

    id: int = Field(description="候选主键")
    live_session_id: int = Field(description="列出它的场次")
    player_id: int = Field(description="说这件事的玩家")
    player_name: str = Field(default="", description="这位玩家最近一次见到时的名字；读不到为空")
    title: str = Field(description="标题（用'他'指他）")
    player_quote: str = Field(description="他那句公屏的逐字片段")
    reply_quote: str = Field(description="她回他的那句的逐字片段")
    reply_in: Literal["game_chat", "speech"] = Field(description="她在哪儿回的：game_chat 游戏公屏 / speech 直播里")
    status: str = Field(description="pending 待审 / approved 已批 / rejected 已驳回")
    created_at_ms: int = Field(description="列出的时刻（Unix 毫秒）")
    decided_at_ms: Optional[int] = Field(default=None, description="批准或驳回的时刻")


class PlayerCandidateListResponse(BaseModel):
    """玩家自述列表与待审数（记忆页玩家自述审核区）。"""

    items: List[PlayerCandidateItem] = Field(default_factory=list, description="自述，新的在前")
    pending_count: int = Field(description="还在待审的条数")


class PlayerCandidateApproveRequest(BaseModel):
    """批准一条玩家自述：可以顺手改标题。"""

    title: Optional[str] = Field(default=None, description="改过的标题（至多 30 字）；空 = 用整理写的")
