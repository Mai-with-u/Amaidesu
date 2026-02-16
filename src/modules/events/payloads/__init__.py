"""
事件 Payload 模块（按域组织）

为 EventBus 事件提供类型安全的 Pydantic Payload 定义。

模块结构：
- input.py: Input Domain 相关 Payload
- decision.py: Decision Domain 相关 Payload
- output.py: Output Domain 相关 Payload

使用示例:
    from src.modules.events.payloads import MessageReadyPayload
    from src.modules.events.names import CoreEvents

    # 发送事件
    await event_bus.emit(
        CoreEvents.INPUT_MESSAGE_READY,
        MessageReadyPayload.from_normalized_message(message),
        source="console_input",
    )

    # 订阅事件（类型提示）
    @event_bus.on(CoreEvents.INPUT_MESSAGE_READY)
    async def handle_message(payload: MessageReadyPayload):
        message = payload.message
        logger.debug(f"收到消息: {message.text}")
"""

from src.modules.logging import get_logger

from .decision import (
    IntentActionPayload,
    IntentPayload,
    ProviderConnectedPayload,
    ProviderDisconnectedPayload,
)
from .input import (
    MessageReadyPayload,
    RawDataPayload,
)
from .output import (
    OBSSendTextPayload,
    OBSSetSourceVisibilityPayload,
    OBSSwitchScenePayload,
    RemoteStreamRequestImagePayload,
)
from .provider import GenericProviderPayload

logger = get_logger("Payloads")

__all__ = [
    # Input Domain
    "RawDataPayload",
    "MessageReadyPayload",
    # Decision Domain
    "IntentPayload",
    "IntentActionPayload",
    "ProviderConnectedPayload",
    "ProviderDisconnectedPayload",
    # Output Domain
    "OBSSendTextPayload",
    "OBSSwitchScenePayload",
    "OBSSetSourceVisibilityPayload",
    "RemoteStreamRequestImagePayload",
    # Provider (通用)
    "GenericProviderPayload",
]
