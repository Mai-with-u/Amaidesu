"""
弹幕小部件模块

提供弹幕小部件服务，用于在 3D 场景中作为道具显示弹幕、礼物、SC 等消息。
用于 Warudo 等虚拟形象软件的网页道具场景。
"""

from .game2048_service import Game2048WidgetService
from .models import DanmakuWidgetMessage, MessageType
from .service import DanmakuWidgetService

__all__ = [
    "DanmakuWidgetMessage",
    "DanmakuWidgetService",
    "Game2048WidgetService",
    "MessageType",
]
