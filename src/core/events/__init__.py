"""
事件系统模块

提供类型安全的事件数据契约系统。
"""

from .registry import EventRegistry
from .names import CoreEvents, PluginEventPrefix
from .models import (
    RawDataEvent,
    NormalizedTextEvent,
    DecisionRequestEvent,
    DecisionResponseEvent,
    IntentGeneratedEvent,
    ExpressionParametersEvent,
    SystemErrorEvent,
)


def register_core_events() -> None:
    """
    注册所有核心事件

    在 AmaidesuCore 初始化时调用。
    """
    # Layer 1: 输入感知
    EventRegistry.register_core_event(CoreEvents.PERCEPTION_RAW_DATA_GENERATED, RawDataEvent)

    # Layer 2: 输入标准化
    EventRegistry.register_core_event(CoreEvents.NORMALIZATION_TEXT_READY, NormalizedTextEvent)

    # Layer 4: 决策层
    EventRegistry.register_core_event(CoreEvents.DECISION_REQUEST, DecisionRequestEvent)
    EventRegistry.register_core_event(CoreEvents.DECISION_RESPONSE_GENERATED, DecisionResponseEvent)

    # Layer 5: 表现理解
    EventRegistry.register_core_event(CoreEvents.UNDERSTANDING_INTENT_GENERATED, IntentGeneratedEvent)

    # Layer 6: 表现生成
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
    "NormalizedTextEvent",
    "DecisionRequestEvent",
    "DecisionResponseEvent",
    "IntentGeneratedEvent",
    "ExpressionParametersEvent",
    "SystemErrorEvent",
    # 初始化函数
    "register_core_events",
]
