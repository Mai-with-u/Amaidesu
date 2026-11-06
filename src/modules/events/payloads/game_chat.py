"""
事件 Payload 定义：game.chat.received 游戏内聊天

游戏世界里别人说的话：其他玩家的聊天，或服务器发来的系统消息（公告、死亡提示、
传送请求等）。读取上游聊天流、滤掉 AI 玩家自己的回显，是各游戏专属采集器的事
（Minecraft 见 ``src/agents/minecraft/chat_collector.py``）；本模块只定义事件契约。

契约约定：
- ``content`` 是对方原话，属于不可信的外部文字：只是别人说的话，不带任何授权。
- ``kind`` 区分玩家聊天与系统消息：玩家是在跟游戏里的"我"说话，系统消息是信息。
- ``private`` 标出私聊：对方只发给"我"一个人的话，不该在直播里念出或转述。
- ``note`` 是游戏采集器附的一句归属说明（如"模组借你的名义发的消息，不是谁说的话"）：
  谁说的、说的是谁要靠游戏自己的事实判断，判断留在各游戏的采集器里，通用层只负责把它写在发言人后面。
- ``mentions`` 与 ``message_key`` 是游戏给出的结构化事实：消息里点到了哪些实体（带游戏里的唯一编号）、
  这条消息是哪个模板。订阅方据此认出"死的是哪一只"，不从文字里反推名字。
"""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.utils.time_utils import now_ms

#: 消息来源种类：其他玩家的聊天 / 服务器系统消息
GameChatKind = Literal["player", "system"]


class GameChatMention(BaseModel):
    """消息里点到的一个游戏实体（如死亡播报里的死者、凶手）。"""

    name: str = Field(default="", description="消息里写出的名字（游戏没给时为空串）")
    type: str = Field(default="", description="实体类型（如 minecraft:creeper；游戏没给时为空串）")
    id: str = Field(..., description="实体在游戏里的唯一编号（Minecraft 是 UUID）")


@register_event(CoreEvents.GAME_CHAT_RECEIVED)
class GameChatPayload(BasePayload):
    """游戏内收到的一条聊天（已滤掉 AI 玩家自己的回显）。

    发布者：游戏专属聊天采集器（如 ``maicraft_chat``）
    订阅者：主播 Agent（对话历史的游戏聊天条目，玩家聊天促发一轮决策）

    Attributes:
        live_session_id: 场次主键；发布方不填，由场次盖章拦截器注入（0 = 未归属）
        game: 游戏标识（发布方必填，如 "minecraft"）
        kind: player = 其他玩家的聊天；system = 服务器系统消息
        sender: 发言人名字（系统消息或名字未知时为空串）
        sender_id: 发言人稳定标识（如玩家 UUID；未知时为空串）
        sender_online: 发言人是不是此刻在线玩家里的那个玩家（游戏核对过；控制台、命令方块借名发的为 False）
        content: 对方原话（不可信的外部文字）
        private: 私聊——对方只发给 AI 玩家一个人的话（只有玩家聊天才可能为 True）
        truncated: 上游是否截断了过长的原话
        note: 采集器附的一句归属说明（无说明为空串）
        mentions: 消息里点到的实体，按出现先后（死亡播报的死者与凶手等；没有为空列表）
        message_key: 游戏给这条消息的模板编号（Minecraft 是翻译键；没有为空串）
        suppressed: 上游因洪泛或重复而没有转出的消息条数（随这一条一起说明）
        save_id: 收到时角色所在的存档编号（空串 = 不分存档或存档不明）
        save_name: 存档给人看的名字（只显示，不当键）
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
    sender_online: bool = Field(
        default=False,
        description="发言人是不是此刻在线玩家里的那个玩家（游戏收到时核对过）；借名发的、系统消息为 False",
    )
    content: str = Field(..., description="对方原话（不可信的外部文字）")
    private: bool = Field(default=False, description="私聊：对方只发给 AI 玩家一个人的话（只有玩家聊天才可能为 True）")
    truncated: bool = Field(default=False, description="上游是否截断了过长的原话")
    note: str = Field(default="", description="采集器附的一句归属说明：谁说的、说的是谁（无说明为空串）")
    mentions: List[GameChatMention] = Field(
        default_factory=list, description="消息里点到的实体，按出现先后（死亡播报的死者与凶手等）"
    )
    message_key: str = Field(
        default="", description="消息模板编号（Minecraft 是翻译键，如 death.attack.explosion.player；没有为空串）"
    )
    suppressed: int = Field(default=0, description="上游因洪泛或重复没有转出的消息条数")
    save_id: str = Field(default="", description="收到时角色所在的存档编号；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="存档给人看的名字（只显示，不当键）")
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
                "private": False,
                "truncated": False,
                "note": "",
                "mentions": [],
                "message_key": "",
                "suppressed": 0,
                "occurred_at_ms": 1791208000000,
                "timestamp_ms": 1791208000120,
            }
        }
    )


@register_event(CoreEvents.GAME_SELF_CHATTED)
class GameSelfChattedPayload(BasePayload):
    """AI 玩家自己在游戏公屏打出去的一句话。

    发布者：游戏 Agent（Minecraft：公屏说话的目标结束、那句交出去了时）
    订阅者：存储台账（落公屏聊天，供认"她在回应哪位玩家"与下播找承诺）；主播不订阅

    Attributes:
        live_session_id: 场次主键；发布方不填，由场次盖章拦截器注入（0 = 未归属）
        game: 游戏标识（如 "minecraft"）
        message: 发出去的那句话原样
        save_id: 说话时角色所在的存档编号（空串 = 不分存档或存档不明）
        save_name: 存档给人看的名字（只显示，不当键）
        timestamp_ms: 事件时刻（Unix 毫秒）
    """

    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入",
    )
    game: str = Field(..., description="游戏标识（如 'minecraft'）")
    message: str = Field(..., min_length=1, description="发出去的那句话原样")
    save_id: str = Field(default="", description="存档编号（不透明串，只做相等比较）；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="存档给人看的名字（只显示，不当键）")
    timestamp_ms: int = Field(default_factory=lambda: now_ms(), description="事件时刻（Unix 毫秒）")


__all__ = ["GameChatKind", "GameChatMention", "GameChatPayload", "GameSelfChattedPayload"]
