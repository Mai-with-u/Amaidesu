"""
事件 Payload 定义：game.place.noted 游戏里的地点动静

游戏 Agent 记下的、和"记住的地点"有关的四种动静：记住一个地点、忘掉一个地点（世界记忆改了），
速览时走近一个记住的地点、离开它（角色眼下在哪）。下播整理据此认出一句话是在哪个地点说的、
本场记住了哪些地点，档案行由此挂上地点；主播走到地点跟前时想起那里的旧事也靠它佐证。

契约约定：
- 只落库，不进主播的对话时间线、不促发决策：主播不订阅本事件，免得她念叨"我记住了某某地方"。
- ``place`` 是游戏里记地点时起的名字，原样转出；``distance`` 只在走近时有（水平距离，格）。
- 存档由发布方按自己读到的世界身份填（同 ``GamePayload``），地点只在那个存档里成立。
"""

from typing import Literal, Optional

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.utils.time_utils import now_ms

#: 地点动静的种类：记住 / 忘掉（世界记忆改了）；走近 / 离开（速览时角色眼下在哪）
GamePlaceKind = Literal["remembered", "forgotten", "near", "left"]


@register_event(CoreEvents.GAME_PLACE_NOTED)
class GamePlacePayload(BasePayload):
    """游戏里一个记住的地点的动静。

    发布者：游戏 Agent（Minecraft：记地点、忘掉地点成功时；速览读到位置时）
    订阅者：存储台账（落 ``game_events``，供下播整理认地点）

    Attributes:
        live_session_id: 场次主键；发布方不填，由场次盖章拦截器注入（0 = 未归属）
        game: 游戏标识（如 "minecraft"）
        kind: remembered / forgotten / near / left
        place: 地点名（游戏里记地点时起的名字）
        distance: 走近时和地点的水平距离（格）；其余种类为 None
        save_id: 动静发生时角色所在的存档编号（空串 = 不分存档或存档不明）
        save_name: 存档给人看的名字（只显示，不当键）
        timestamp_ms: 事件时刻（Unix 毫秒）
    """

    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入",
    )
    game: str = Field(..., description="游戏标识（如 'minecraft'）")
    kind: GamePlaceKind = Field(..., description="地点动静的种类")
    place: str = Field(..., min_length=1, description="地点名（游戏里记地点时起的名字）")
    distance: Optional[int] = Field(default=None, ge=0, description="走近时和地点的水平距离（格）；其余为 None")
    save_id: str = Field(default="", description="存档编号（不透明串，只做相等比较）；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="存档给人看的名字（只显示，不当键）")
    timestamp_ms: int = Field(default_factory=lambda: now_ms(), description="事件时刻（Unix 毫秒）")
