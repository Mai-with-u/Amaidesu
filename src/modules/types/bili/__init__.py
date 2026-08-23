"""B 站消息类型 —— 公共共享类型（v2 / Wave 5 迁移）

按 §1.46 + .omo/drafts/amaidesu-v2-migration.md §C：从
``src/stages/input/shared/bili_messages/`` 升为公共共享类型，供 B 站相关
采集器（官方/旧版）共用。

不依赖采集器/事件总线，可被任何模块安全 import。
"""

from src.modules.types.bili.base import BiliBaseMessage, BiliMessageType
from src.modules.types.bili.config import BiliMessageTypeConfig
from src.modules.types.bili.danmaku import DanmakuMessage
from src.modules.types.bili.enter import EnterMessage
from src.modules.types.bili.gift import (
    AnchorInfo,
    BlindGift,
    ComboInfo,
    GiftMessage,
)
from src.modules.types.bili.guard import GuardMessage, UserInfo
from src.modules.types.bili.superchat import SuperChatMessage

# BiliRawMessage Union 类型
BiliRawMessage = DanmakuMessage | GiftMessage | SuperChatMessage | GuardMessage | EnterMessage

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
]
