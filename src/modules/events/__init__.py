"""
Amaidesu 事件系统模块

包含 EventBus 事件总线实现。
"""

# 导出核心组件
from .event_bus import EventBus
from .registry import register_core_events

__all__ = [
    "EventBus",
    "register_core_events",
]
