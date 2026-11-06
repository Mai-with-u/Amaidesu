"""
事件 Payload 定义：game.player.interacted 别的玩家和 AI 玩家发生了一次互动

游戏 Agent 把游戏里别的玩家冲 AI 玩家做的事读成一条条结构化的互动：打招呼（冲它蹲起、挥手、拍一下）、
送东西（朝它扔东西、落在它身边）、切磋（谁赢、各打中几下）。只给记忆落库：存储台账据此认出这位玩家、
记下他和她有过什么来往，下一场他再出现时主播认得他。

契约约定：
- 只落库，不进主播的对话时间线、不促发决策：主播不订阅本事件。同一个动作主播已经从
  ``game.body.player_body_language`` 一条条知道了，这里不是第二份叙事。
- 认人按 ``player_uuid``（游戏给每个账号的 UUID）；只知道名字时（旧版 Mod 的切磋结果不带对手，
  只有下令时按名字点的对手）为空，由订阅方按"本场见过的、叫这个名字的只有一位"去对。
- 不带私聊内容、不带玩家说他自己的事；``fact`` 只放互动本身的事实。
- 存档由发布方按自己读到的世界身份填（同 ``GamePayload``）。
"""

from typing import Any, Dict, List, Literal

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.utils.time_utils import now_ms

#: 互动种类：打招呼 / 送东西 / 切磋
GamePlayerInteractionKind = Literal["greet", "gift", "spar"]


@register_event(CoreEvents.GAME_PLAYER_INTERACTED)
class GamePlayerInteractedPayload(BasePayload):
    """别的玩家和 AI 玩家的一次互动。

    发布者：游戏专属采集器与游戏 Agent（Minecraft：肢体动作事件、切磋收手）
    订阅者：存储台账（认人、记互动）

    Attributes:
        live_session_id: 场次主键；发布方不填，由场次盖章拦截器注入（0 = 未归属）
        game: 游戏标识（如 "minecraft"）
        kind: greet / gift / spar
        player_uuid: 游戏给这个账号的 UUID；只知道名字时为空串
        player_name: 那一刻的名字
        actions: 打招呼时做了哪几种动作（蹲起、挥手……）；其余种类为空
        fact: 互动本身的事实（送东西：那句"朝你扔了 3 个面包"；切磋：谁赢、各打中几下）
        save_id: 互动发生时角色所在的存档编号（空串 = 不分存档或存档不明）
        save_name: 存档给人看的名字（只显示，不当键）
        timestamp_ms: 事件时刻（Unix 毫秒）
    """

    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入",
    )
    game: str = Field(..., description="游戏标识（如 'minecraft'）")
    kind: GamePlayerInteractionKind = Field(..., description="互动种类")
    player_uuid: str = Field(default="", description="游戏给这个账号的 UUID；只知道名字时为空串")
    player_name: str = Field(default="", description="那一刻的名字")
    actions: List[str] = Field(default_factory=list, description="打招呼时做了哪几种动作；其余种类为空")
    fact: Dict[str, Any] = Field(default_factory=dict, description="互动本身的事实")
    save_id: str = Field(default="", description="存档编号（不透明串，只做相等比较）；空串 = 不分存档或存档不明")
    save_name: str = Field(default="", description="存档给人看的名字（只显示，不当键）")
    timestamp_ms: int = Field(default_factory=lambda: now_ms(), description="事件时刻（Unix 毫秒）")
