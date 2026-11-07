"""游戏里的人域数据契约（game_players 及其互动、公屏聊天、观众关联的读写模型）

她在游戏里遇到的别的玩家按游戏给每个账号的 UUID 认：同一个 UUID 改了名仍是同一个人，同名不同 UUID
是两个人。只自动记和她发生过的互动，都是她亲身经历、直播画面里看得到的事；玩家说的关于他自己的事
要人批准才记。玩家记忆只给主播，不给游戏 Agent。

时间字段统一毫秒（Unix epoch 毫秒）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.modules.types.game_save import OnlineMode

__all__ = [
    "GAME_CHAT_OTHER",
    "GAME_CHAT_PLAYER",
    "GAME_CHAT_STREAMER",
    "INTERACTION_GIFT",
    "INTERACTION_GREET",
    "INTERACTION_HELPED",
    "INTERACTION_KINDS",
    "INTERACTION_SPAR",
    "INTERACTION_TOGETHER",
    "GameChatDirection",
    "GameChatLine",
    "GamePlayer",
    "PendingPlayerCandidate",
    "PlayerCandidate",
    "PlayerInteraction",
    "PlayerSave",
    "ViewerPlayerLink",
]

# 和她的互动种类：打招呼（冲她蹲起挥手、公屏点她名说话或她回应了他，按场合并计数）、送东西
# （朝她扔东西落在她身边）、切磋（谁赢、几局）、一起做的事（委派任务点名了这位玩家）、帮过忙
INTERACTION_GREET = "greet"
INTERACTION_GIFT = "gift"
INTERACTION_SPAR = "spar"
INTERACTION_TOGETHER = "together"
INTERACTION_HELPED = "helped"
INTERACTION_KINDS = (INTERACTION_GREET, INTERACTION_GIFT, INTERACTION_SPAR, INTERACTION_TOGETHER, INTERACTION_HELPED)

# 公屏聊天的方向：玩家说的（挂玩家）/ 她说的 / 认不出是谁说的（没有 UUID 或不在线的借名消息，
# 只占一行位置、不存原话：判断"她这句之前有没有别人插话"要知道有人说过话）
GAME_CHAT_PLAYER = "player"
GAME_CHAT_STREAMER = "streamer"
GAME_CHAT_OTHER = "other"
GameChatDirection = Literal["player", "streamer", "other"]


class PlayerSave(BaseModel):
    """见过这位玩家的一个存档。"""

    model_config = ConfigDict(extra="forbid")

    save_id: str = Field(..., description="存档编号（游戏报的世界编号）")
    save_name: str = Field(default="", description="存档给人看的名字")
    online_mode: OnlineMode = Field(
        default="unknown",
        description="这个存档的服务器验不验账号：off 时按名字认人，改名算新人，冒名认不出；unknown 没报",
    )


class GamePlayer(BaseModel):
    """``game_players`` 一行：一位按 UUID 认出的玩家。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="玩家主键")
    game: str = Field(..., description="哪个游戏（minecraft）")
    uuid: str = Field(..., description="游戏给这个账号的 UUID")
    name: str = Field(..., description="最近一次见到时的名字")
    names: List[str] = Field(default_factory=list, description="见过的名字，按第一次见到的先后（含当前名字）")
    saves: List[PlayerSave] = Field(default_factory=list, description="见过他的存档，按第一次见到的先后")
    first_session_id: Optional[int] = Field(default=None, description="第一次见到他的场次；场次被删时为空")
    first_seen_ms: int = Field(..., description="第一次见到的时刻")
    last_session_id: Optional[int] = Field(default=None, description="最近一次见到他的场次；场次被删时为空")
    last_seen_ms: int = Field(..., description="最近一次见到的时刻")
    forget_requested_at_ms: Optional[int] = Field(
        default=None, description="他在游戏公屏发'忘了我'的时刻；宽限期内不再记他、不推他的卡，None 即没发过"
    )

    def former_names(self) -> List[str]:
        """以前用过、和现在不同的名字（卡片写"以前叫某某"）。"""
        return [name for name in self.names if name != self.name]


class PlayerInteraction(BaseModel):
    """``game_player_interactions`` 一行：和她的一次互动（打招呼一场合并成一行）。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="互动主键")
    player_id: int = Field(..., description="哪位玩家")
    live_session_id: int = Field(..., description="哪一场")
    save_id: str = Field(default="", description="在哪个存档；不明为空")
    save_name: str = Field(default="", description="存档给人看的名字")
    kind: str = Field(..., description="greet / gift / spar / together / helped")
    fact: Dict[str, Any] = Field(default_factory=dict, description="结构化事实：次数、物品与数量、谁赢几局、任务标题")
    at_ms: int = Field(..., description="互动时刻（打招呼是这一场最近的一次）")


class GameChatLine(BaseModel):
    """``game_chat`` 一行：一条公屏游戏聊天（私聊不落库）。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="聊天主键")
    live_session_id: int = Field(..., description="哪一场")
    game: str = Field(..., description="哪个游戏")
    direction: GameChatDirection = Field(..., description="player 玩家说的 / streamer 她说的 / other 认不出是谁")
    player_id: Optional[int] = Field(default=None, description="玩家说的挂玩家；她说的、认不出的为空")
    content: str = Field(default="", description="原话；认不出是谁说的不存原话")
    save_id: str = Field(default="", description="在哪个存档；不明为空")
    save_name: str = Field(default="", description="存档给人看的名字")
    at_ms: int = Field(..., description="收到或发出的时刻")


class PendingPlayerCandidate(BaseModel):
    """整理校验通过、等着写进待审区的一条玩家自述（批准前对她完全不可见）。

    挂在他那句公屏聊天和她的回应上：回应是她在公屏打的那句（``reply_game_chat_id``）或播出的那句台词
    （``reply_live_chat_id``），两者恰有一个。
    """

    model_config = ConfigDict(extra="forbid")

    player_id: int = Field(..., description="说这件事的玩家")
    title: str = Field(..., description="标题（玩家代号已换成「他」；代码已截到上限）")
    player_chat_id: int = Field(..., description="他那句公屏聊天（game_chat 行）")
    player_quote: str = Field(..., description="他那句的逐字片段")
    reply_game_chat_id: Optional[int] = Field(default=None, description="她在公屏回他的那句；回应是台词时为空")
    reply_live_chat_id: Optional[int] = Field(default=None, description="她播出的那句回应台词；回应在公屏时为空")
    reply_quote: str = Field(..., description="她那句回应的逐字片段")


class PlayerCandidate(PendingPlayerCandidate):
    """``player_candidates`` 一行：待审、已批或已驳回的玩家自述。"""

    id: int = Field(..., description="候选主键")
    live_session_id: int = Field(..., description="列出它的场次")
    status: str = Field(..., description="pending 待审 / approved 已批 / rejected 已驳回")
    created_at_ms: int = Field(..., description="列出的时刻")
    decided_at_ms: Optional[int] = Field(default=None, description="批准或驳回的时刻")


class ViewerPlayerLink(BaseModel):
    """``viewer_player_links`` 一行：运营者确认的观众与玩家对应（只用来连带删除，从不告诉她）。"""

    model_config = ConfigDict(extra="forbid")

    viewer_id: int = Field(..., description="直播间观众")
    player_id: int = Field(..., description="游戏玩家")
    confirmed_at_ms: int = Field(..., description="运营者确认的时刻")
