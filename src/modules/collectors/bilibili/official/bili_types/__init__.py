"""B 站官方协议消息类型

官方弹幕采集器的解析形状（弹幕/礼物/舰长/SC 等），仅本采集器域使用；
不依赖采集器框架/事件总线，可被任何模块安全 import。
"""

from src.modules.collectors.bilibili.official.bili_types.base import BiliBaseMessage, BiliMessageType
from src.modules.collectors.bilibili.official.bili_types.config import BiliMessageTypeConfig
from src.modules.collectors.bilibili.official.bili_types.danmaku import DanmakuMessage
from src.modules.collectors.bilibili.official.bili_types.enter import EnterMessage
from src.modules.collectors.bilibili.official.bili_types.gift import (
    AnchorInfo,
    BlindGift,
    ComboInfo,
    GiftMessage,
)
from src.modules.collectors.bilibili.official.bili_types.guard import GuardMessage, UserInfo
from src.modules.collectors.bilibili.official.bili_types.superchat import SuperChatMessage
from src.modules.collectors.bilibili.official.bili_types.watched_change import WatchedChangeMessage

# BiliRawMessage Union 类型
BiliRawMessage = DanmakuMessage | GiftMessage | SuperChatMessage | GuardMessage | EnterMessage | WatchedChangeMessage

__all__ = [
    "AnchorInfo",
    "BlindGift",
    "BiliBaseMessage",
    "BiliMessageType",
    "BiliMessageTypeConfig",
    "BiliRawMessage",
    "ComboInfo",
    "DanmakuMessage",
    "EnterMessage",
    "GiftMessage",
    "GuardMessage",
    "SuperChatMessage",
    "UserInfo",
    "WatchedChangeMessage",
]
