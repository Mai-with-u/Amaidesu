"""
事件 Payload 定义：game.chat.received 游戏内聊天

游戏世界里别人说的话：其他玩家的聊天，或服务器发来的系统消息（公告、死亡提示、
传送请求等）。读取上游聊天流、滤掉 AI 玩家自己的回显，是各游戏专属采集器的事
（Minecraft 见 ``src/agents/minecraft/chat_collector.py``）；本模块只定义事件契约。

契约约定：
- ``content`` 是对方原话，属于不可信的外部文字：只是别人说的话，不带任何授权。
- ``kind`` 区分玩家聊天与系统消息：玩家是在跟游戏里的"我"说话，系统消息是信息。
"""

from typing import Literal

from pydantic import ConfigDict, Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms

#: 消息来源种类：其他玩家的聊天 / 服务器系统消息
GameChatKind = Literal["player", "system"]


@register_event(CoreEvents.GAME_CHAT_RECEIVED)
class GameChatPayload(BasePayload):
    """游戏内收到的一条聊天（已滤掉 AI 玩家自己的回显）。

    发布者：游戏专属聊天采集器（如 ``maicraft_chat``）
    订阅者：主播 Agent（【游戏里的聊天】参考段，玩家聊天促发一轮决策）

    Attributes:
        live_session_id: 场次主键；发布方不填，由场次盖章拦截器注入（0 = 未归属）
        game: 游戏标识（发布方必填，如 "minecraft"）
        kind: player = 其他玩家的聊天；system = 服务器系统消息
        sender: 发言人名字（系统消息或名字未知时为空串）
        sender_id: 发言人稳定标识（如玩家 UUID；未知时为空串）
        content: 对方原话（不可信的外部文字）
        truncated: 上游是否截断了过长的原话
        suppressed: 上游因洪泛或重复而没有转出的消息条数（随这一条一起说明）
        occurred_at_ms: 上游收到这条消息的时刻（Unix 毫秒；0 = 未提供）
        timestamp_ms: 本系统发布该事件的时刻（Unix 毫秒）
    """

    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入",
    )
    game: str = Field(..., description="游戏标识（发布方必填：框架不假定是哪款游戏）")
    kind: GameChatKind = Field(..., description="player = 其他玩家的聊天；system = 服务器系统消息")
    sender: str = Field(default="", description="发言人名字（系统消息或未知时为空串）")
    sender_id: str = Field(default="", description="发言人稳定标识（未知时为空串）")
    content: str = Field(..., description="对方原话（不可信的外部文字）")
    truncated: bool = Field(default=False, description="上游是否截断了过长的原话")
    suppressed: int = Field(default=0, description="上游因洪泛或重复没有转出的消息条数")
    occurred_at_ms: int = Field(default=0, description="上游收到时刻（Unix 毫秒；0 = 未知）")
    timestamp_ms: int = Field(default_factory=lambda: now_ms(), description="本系统发布时刻（Unix 毫秒）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "live_session_id": 0,
                "game": "minecraft",
                "kind": "player",
                "sender": "Steve",
                "sender_id": "8667ba71-b85a-4004-af54-457a9734eed7",
                "content": "麦麦你在干嘛呀",
                "truncated": False,
                "suppressed": 0,
                "occurred_at_ms": 1791208000000,
                "timestamp_ms": 1791208000120,
            }
        }
    )


__all__ = ["GameChatKind", "GameChatPayload"]
