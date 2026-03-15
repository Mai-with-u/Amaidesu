"""
弹幕小部件模块

提供弹幕小部件服务，用于在 3D 场景中作为道具显示弹幕、礼物、SC 等消息。
用于 Warudo 等虚拟形象软件的网页道具场景。
"""

from .models import DanmakuWidgetMessage, MessageType
from .service import DanmakuWidgetService

__all__ = ["DanmakuWidgetMessage", "MessageType", "DanmakuWidgetService"]
