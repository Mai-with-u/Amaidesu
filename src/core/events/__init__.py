"""
事件系统模块（5层架构）

提供类型安全的事件数据契约系统。
"""

from .registry import EventRegistry
from .names import CoreEvents, PluginEventPrefix
from .models import (
    RawDataEvent,
    NormalizedMessageEvent,
    DecisionRequestEvent,
    DecisionResponseEvent,
    IntentGeneratedEvent,
    ExpressionParametersEvent,
    SystemErrorEvent,
)


def register_core_events() -> None:
    """
    注册所有核心事件（5层架构）

    在 AmaidesuCore 初始化时调用。
    """
    # Layer 1-2: 输入层
    EventRegistry.register_core_event(CoreEvents.PERCEPTION_RAW_DATA_GENERATED, RawDataEvent)
    EventRegistry.register_core_event(CoreEvents.NORMALIZATION_MESSAGE_READY, NormalizedMessageEvent)

    # Layer 3: 决策层
    EventRegistry.register_core_event(CoreEvents.DECISION_REQUEST, DecisionRequestEvent)
    EventRegistry.register_core_event(CoreEvents.DECISION_INTENT_GENERATED, IntentGeneratedEvent)

    # Layer 4-5: 参数和渲染
    EventRegistry.register_core_event(CoreEvents.EXPRESSION_PARAMETERS_GENERATED, ExpressionParametersEvent)

    # 系统事件
    EventRegistry.register_core_event(CoreEvents.CORE_ERROR, SystemErrorEvent)


__all__ = [
    # 注册表
    "EventRegistry",
    # 事件名常量
    "CoreEvents",
    "PluginEventPrefix",
    # 事件模型
    "RawDataEvent",
    "NormalizedMessageEvent",
    "DecisionRequestEvent",
    "DecisionResponseEvent",
    "IntentGeneratedEvent",
    "ExpressionParametersEvent",
    "SystemErrorEvent",
    # 初始化函数
    "register_core_events",
]
