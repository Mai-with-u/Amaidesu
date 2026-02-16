"""
Dashboard WebSocket 模块

处理实时事件推送。
"""

from src.modules.dashboard.websocket.broadcaster import EventBroadcaster
from src.modules.dashboard.websocket.handler import WebSocketHandler

__all__ = ["WebSocketHandler", "EventBroadcaster"]
