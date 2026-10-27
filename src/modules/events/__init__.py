"""
Amaidesu 事件系统模块

包含 EventBus 事件总线实现。
"""

# 导出核心组件
from .event_bus import EventBus
from .registry import (
    EVENT_REGISTRY,
    ensure_registry_consistency,
    get_registered_event,
    list_registered_events,
    register_core_events,
    register_event,
)

__all__ = [
    "EventBus",
    "EVENT_REGISTRY",
    "ensure_registry_consistency",
    "register_event",
    "get_registered_event",
    "list_registered_events",
    "register_core_events",
]
