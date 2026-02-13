"""
B站消息类型

共享的 B 站弹幕消息数据类型，用于两个 Provider。
"""

from typing import Union

from .base import BiliBaseMessage, BiliMessageType
from .config import BiliMessageTypeConfig
from .danmaku import DanmakuMessage
from .enter import EnterMessage
from .gift import GiftMessage
from .guard import GuardMessage
from .superchat import SuperChatMessage

# BiliRawMessage Union 类型
BiliRawMessage = Union[
    DanmakuMessage,
    GiftMessage,
    SuperChatMessage,
    GuardMessage,
    EnterMessage,
]

__all__ = [
    "BiliBaseMessage",
    "BiliMessageType",
    "BiliMessageTypeConfig",
    "BiliRawMessage",
    "DanmakuMessage",
    "EnterMessage",
    "GiftMessage",
    "GuardMessage",
    "SuperChatMessage",
]
