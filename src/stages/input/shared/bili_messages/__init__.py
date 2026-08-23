"""
旧路径 bili_messages —— 仅作为 W5 迁移期间的向后兼容 re-export 垫片

迁移目标：``src/modules/types/bili/``（v2 / Wave 5 升为公共共享类型）。

新代码请直接 ``from src.modules.types.bili import ...``。
"""

from src.modules.types.bili import (  # noqa: F401
    AnchorInfo,
    BlindGift,
    BiliBaseMessage,
    BiliMessageType,
    BiliMessageTypeConfig,
    BiliRawMessage,
    ComboInfo,
    DanmakuMessage,
    EnterMessage,
    GiftMessage,
    GuardMessage,
    SuperChatMessage,
    UserInfo,
)

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
