"""
事件 Payload 模块（按域组织）

为 EventBus 事件提供类型安全的 Pydantic Payload 定义。

模块结构：
- core.py: Core 系统事件 Payload（core.startup/shutdown/error）
- input.py: Input 阶段 相关 Payload（MessageReadyPayload）
- decision.py: Decision 阶段 相关 Payload（Intent/Connected/Disconnected）
- output.py: Output 阶段 相关 Payload（OBS/Sticker/IntentDispatched/HandlerCompleted）
- connection.py: 通用组件事件 Payload（ConnectionEventPayload）
- live.py: v2 语义域 — 场次生命周期（live.started/live.ended）
- room.py: v2 语义域 — 直播间行为流（room.message.*）
- game.py: v2 语义域 — 游戏里程碑（game.*）
- agenda.py: v2 语义域 — Agenda 运行进度（agenda.update）
- planner.py: v2 语义域 — 空转检查点（planner.checkpoint）
- tool_result.py: v2 语义域 — 异步工具结果（tool.result.*，不绑定具体名）

使用示例:
    from src.modules.events.payloads import MessageReadyPayload
    from src.modules.events.names import CoreEvents

    # 发送事件
    await event_bus.emit(
        CoreEvents.INPUT_MESSAGE_RECEIVED,
        MessageReadyPayload.from_normalized_message(message),
        source="console_input",
    )

    # 订阅事件（类型提示）
    @event_bus.on(CoreEvents.INPUT_MESSAGE_RECEIVED)
    async def handle_message(payload: MessageReadyPayload):
        message = payload.message
        logger.debug(f"收到消息: {message.text}")
"""

from src.modules.logging import get_logger

from .agenda import (
    AgendaItem,
    AgendaPayload,
)
from .connection import ConnectionEventPayload
from .core import (
    CoreErrorPayload,
    CoreShutdownPayload,
    CoreStartupPayload,
)
from .decision import (
    IntentActionPayload,
    IntentPayload,
    ConnectedPayload,
    DisconnectedPayload,
)
from .game import GamePayload
from .input import (
    MessageReadyPayload,
)
from .live import LivePayload
from .output import (
    OBSCommandPayload,
    OutputHandlerCompletedPayload,
    OutputIntentDispatchedPayload,
    StickerCommandPayload,
)
from .planner import (
    CheckpointAgendaPosition,
    CheckpointPayload,
)
from .room import (
    GiftInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)
from .tool_result import ToolResultPayload

logger = get_logger("Payloads")

__all__ = [
    # Core 系统事件
    "CoreStartupPayload",
    "CoreShutdownPayload",
    "CoreErrorPayload",
    # Input 阶段
    "MessageReadyPayload",
    # Decision 阶段
    "IntentPayload",
    "IntentActionPayload",
    "ConnectedPayload",
    "DisconnectedPayload",
    # Output 阶段
    "OBSCommandPayload",
    "OutputHandlerCompletedPayload",
    "OutputIntentDispatchedPayload",
    "StickerCommandPayload",
    # 组件 (通用)
    "ConnectionEventPayload",
    # v2 语义域 — live
    "LivePayload",
    # v2 语义域 — room.message.*
    "RoomMessageUser",
    "GiftInfo",
    "SuperChatInfo",
    "RoomMessagePayload",
    # v2 语义域 — game.*
    "GamePayload",
    # v2 语义域 — agenda
    "AgendaItem",
    "AgendaPayload",
    # v2 语义域 — planner
    "CheckpointAgendaPosition",
    "CheckpointPayload",
    # v2 语义域 — tool.result.*
    "ToolResultPayload",
]
