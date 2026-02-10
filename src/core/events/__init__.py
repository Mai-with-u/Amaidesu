"""
事件系统模块（3域架构）

提供类型安全的事件数据契约系统。

模块结构：
- registry: 事件注册表
- names: 事件名称常量
- payloads: 按 Domain 组织的 Payload 定义
"""

from .registry import EventRegistry
from .names import CoreEvents

# 导出 Payload
from .payloads import (
    # Input Domain
    RawDataPayload,
    MessageReadyPayload,
    # Decision Domain
    DecisionRequestPayload,
    IntentPayload,
    DecisionResponsePayload,
    ProviderConnectedPayload,
    ProviderDisconnectedPayload,
    # Output Domain
    ParametersGeneratedPayload,
    RenderCompletedPayload,
    RenderFailedPayload,
    # System
    StartupPayload,
    ShutdownPayload,
    ErrorPayload,
)


def register_core_events() -> None:
    """
    注册所有核心事件（3域架构）

    在 main.py 中初始化 EventBus 后调用。
    """
    # Input Domain: 输入域
    EventRegistry.register_core_event(CoreEvents.PERCEPTION_RAW_DATA_GENERATED, RawDataPayload)
    EventRegistry.register_core_event(CoreEvents.NORMALIZATION_MESSAGE_READY, MessageReadyPayload)

    # Decision Domain: 决策域
    EventRegistry.register_core_event(CoreEvents.DECISION_REQUEST, DecisionRequestPayload)
    EventRegistry.register_core_event(CoreEvents.DECISION_INTENT_GENERATED, IntentPayload)
    EventRegistry.register_core_event(CoreEvents.DECISION_RESPONSE_GENERATED, DecisionResponsePayload)
    EventRegistry.register_core_event(CoreEvents.DECISION_PROVIDER_CONNECTED, ProviderConnectedPayload)
    EventRegistry.register_core_event(CoreEvents.DECISION_PROVIDER_DISCONNECTED, ProviderDisconnectedPayload)

    # Output Domain: 输出域
    EventRegistry.register_core_event(CoreEvents.EXPRESSION_PARAMETERS_GENERATED, ParametersGeneratedPayload)
    EventRegistry.register_core_event(CoreEvents.RENDER_COMPLETED, RenderCompletedPayload)
    EventRegistry.register_core_event(CoreEvents.RENDER_FAILED, RenderFailedPayload)

    # 系统事件
    EventRegistry.register_core_event(CoreEvents.CORE_STARTUP, StartupPayload)
    EventRegistry.register_core_event(CoreEvents.CORE_SHUTDOWN, ShutdownPayload)
    EventRegistry.register_core_event(CoreEvents.CORE_ERROR, ErrorPayload)


__all__ = [
    # 注册表
    "EventRegistry",
    # 事件名常量
    "CoreEvents",
    # Payload
    "RawDataPayload",
    "MessageReadyPayload",
    "DecisionRequestPayload",
    "IntentPayload",
    "DecisionResponsePayload",
    "ProviderConnectedPayload",
    "ProviderDisconnectedPayload",
    "ParametersGeneratedPayload",
    "RenderCompletedPayload",
    "RenderFailedPayload",
    "StartupPayload",
    "ShutdownPayload",
    "ErrorPayload",
    # 初始化函数
    "register_core_events",
]
