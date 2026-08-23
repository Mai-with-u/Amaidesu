"""
v2 语义域事件 Payload 定义：room.message.* 直播间行为流

定义 4 类直播间"行为流"事件 Payload（弹幕/礼物/SC/进房）。
对应存储 ``live_chat`` / ``gifts`` / ``super_chats`` 表。

按契约（.omo/drafts/amaidesu-v2-event-contract.md "room.message.*" 节）：
- 统一 ``RoomMessagePayload`` + ``message_type`` 判别（与存储 ``live_chat.message_type`` 一致）
- ``room.message.#`` 通配订阅时按 ``message_type`` 分发
- 礼物/SC 放在结构化字段（``gift``/``sc``），不混进 ``content``
- 所有时间字段统一毫秒（``timestamp_ms``）

注意：
- ``room.state.*`` 是**预留层**，本模块不定义其事件（见 .omo/drafts/amaidesu-v2-event-naming.md ②行为/状态分层）
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


class RoomMessageUser(BaseModel):
    """
    直播间消息发送者信息（嵌套子结构）

    Attributes:
        id: 用户唯一 ID（平台 user_id）
        name: 用户昵称（显示名）
    """

    id: str = Field(..., description="用户唯一 ID（平台 user_id）")
    name: str = Field(..., description="用户昵称（显示名）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"id": "12345", "name": "观众A"},
        }
    )


class GiftInfo(BaseModel):
    """
    礼物信息（嵌套子结构）

    Attributes:
        name: 礼物名称（如 "小星星"）
        count: 礼物数量（一次可送多个）
    """

    name: str = Field(..., description="礼物名称")
    count: int = Field(default=1, ge=1, description="礼物数量")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "小星星", "count": 1},
        }
    )


class SuperChatInfo(BaseModel):
    """
    SuperChat 信息（嵌套子结构）

    Attributes:
        amount: SC 金额（元），浮点（保留精度足够覆盖 SC 区间）
    """

    amount: float = Field(..., ge=0.0, description="SC 金额（元）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"amount": 50.0},
        }
    )


@register_event("room.message.danmaku")
@register_event("room.message.gift")
@register_event("room.message.super_chat")
@register_event("room.message.enter")
class RoomMessagePayload(BasePayload):
    """
    直播间行为流事件 Payload（统一形状 + message_type 判别）

    事件名：
    - ``room.message.danmaku`` — 弹幕（应填充 ``content``）
    - ``room.message.gift`` — 礼物（应填充 ``gift``）
    - ``room.message.super_chat`` — SC（应填充 ``content`` + ``sc``）
    - ``room.message.enter`` — 进房（无内容）

    发布者：直播接入层（弹幕/礼物/SC/进房接收 → 结构化）
    订阅者：Planner（订 ``room.message.danmaku`` 高价值醒来）、后台记账器（订 ``room.*`` 写 live_sessions 状态）

    Attributes:
        live_session_id: 场次 ID（与存储 live_chat FK 一致）
        message_type: 消息类型（Literal 与存储 live_chat.message_type 一致；通配订阅 ``room.message.#`` 时按此分发）
        user: 发送者信息
        content: 文本内容（弹幕/SC 文本；其他类型为空）
        gift: 礼物信息（仅 ``gift`` 类型有值）
        sc: SC 信息（仅 ``super_chat`` 类型有值）
        timestamp_ms: 事件时间戳（Unix 毫秒）
    """

    live_session_id: str = Field(..., description="场次唯一 ID")
    message_type: Literal["danmaku", "gift", "super_chat", "enter"] = Field(
        ...,
        description="消息类型。通配订阅 room.message.# 时按此字段分发（与存储 live_chat.message_type 枚举一致）",
    )
    user: RoomMessageUser = Field(..., description="发送者信息")
    content: str = Field(default="", description="文本内容（弹幕/SC 文本；其他类型为空字符串）")
    gift: Optional[GiftInfo] = Field(default=None, description="礼物信息（仅 gift 类型）")
    sc: Optional[SuperChatInfo] = Field(default=None, description="SuperChat 信息（仅 super_chat 类型）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件时间戳（Unix 毫秒）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "live_session_id": "ls_20260822_001",
                "message_type": "danmaku",
                "user": {"id": "12345", "name": "观众A"},
                "content": "主播好可爱！",
                "gift": None,
                "sc": None,
                "timestamp_ms": 1706745600000,
            }
        }
    )


__all__ = [
    "RoomMessageUser",
    "GiftInfo",
    "SuperChatInfo",
    "RoomMessagePayload",
]
