"""
事件 Payload 模块（按域组织）

为 EventBus 事件提供类型安全的 Pydantic Payload 定义。

模块结构：
- input.py: Input Domain 相关 Payload
- decision.py: Decision Domain 相关 Payload
- output.py: Output Domain 相关 Payload
- system.py: 系统事件 Payload

使用示例:
    from src.core.events.payloads import RawDataPayload, MessageReadyPayload
    from src.core.events.names import CoreEvents

    # 发送事件
    await event_bus.emit(
        CoreEvents.PERCEPTION_RAW_DATA_GENERATED,
        RawDataPayload(content=data, source="console_input", data_type="text")
    )

    # 订阅事件（类型提示）
    @event_bus.on(CoreEvents.NORMALIZATION_MESSAGE_READY)
    async def handle_message(payload: MessageReadyPayload):
        message = payload.message
        print(f"收到消息: {message.text}")
"""

from .input import (
    RawDataPayload,
    MessageReadyPayload,
)
from .decision import (
    DecisionRequestPayload,
    IntentPayload,
    DecisionResponsePayload,
    ProviderConnectedPayload,
    ProviderDisconnectedPayload,
)
from .output import (
    ParametersGeneratedPayload,
    RenderCompletedPayload,
    RenderFailedPayload,
)
from .system import (
    StartupPayload,
    ShutdownPayload,
    ErrorPayload,
)

__all__ = [
    # Input Domain
    "RawDataPayload",
    "MessageReadyPayload",
    # Decision Domain
    "DecisionRequestPayload",
    "IntentPayload",
    "DecisionResponsePayload",
    "ProviderConnectedPayload",
    "ProviderDisconnectedPayload",
    # Output Domain
    "ParametersGeneratedPayload",
    "RenderCompletedPayload",
    "RenderFailedPayload",
    # System
    "StartupPayload",
    "ShutdownPayload",
    "ErrorPayload",
]
