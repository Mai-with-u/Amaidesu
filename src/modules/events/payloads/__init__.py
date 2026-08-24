"""
事件 Payload 模块（按域组织 / Wave 6）

为 EventBus 事件提供类型安全的 Pydantic Payload 定义。

Wave 6 变更：
- 删除 decision.py / output.py（无 Intent；Stage-glue 胶水事件不再使用）
- 删除 IntentPayload / IntentActionPayload / OutputIntentDispatchedPayload /
  OutputHandlerCompletedPayload / OBSCommandPayload / StickerCommandPayload
  （Wave 6 决策出口 = reply 工具调用，无 Intent；OBS/Sticker → 工具直接调用）
- 保留 ConnectedPayload / DisconnectedPayload（RoomMessagePayload 含同构字段，
  旧连接/断开事件已删除 → 仅供向后兼容导入）

模块结构（Wave 6）：
- core.py: Core 系统事件 Payload（core.startup/shutdown/error）
- connection.py: 通用组件事件 Payload（ConnectionEventPayload）
- live.py: v2 语义域 — 场次生命周期（live.started/live.ended）
- room.py: v2 语义域 — 直播间行为流（room.message.*）
- game.py: v2 语义域 — 游戏里程碑（game.*）
- agenda.py: v2 语义域 — Agenda 运行进度（agenda.update）
- planner.py: v2 语义域 — 空转检查点（planner.checkpoint）
- tool_result.py: v2 语义域 — 异步工具结果（tool.result.*，不绑定具体名）

使用示例:
    from src.modules.events.payloads import RoomMessagePayload
    from src.modules.events.names import CoreEvents

    # 发送事件
    await event_bus.emit(
        CoreEvents.ROOM_MESSAGE_DANMAKU,
        RoomMessagePayload(...),
        source="bilibili_danmaku",
    )

    # 订阅事件（类型提示）
    @event_bus.on(CoreEvents.ROOM_MESSAGE_DANMAKU)
    async def handle_danmaku(payload: RoomMessagePayload):
        logger.debug(f"收到弹幕: {payload.content}")
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
from .game import GamePayload
from .live import LivePayload
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
from .sticker import StickerCommandPayload
from .tool_result import ToolResultPayload

logger = get_logger("Payloads")

__all__ = [
    # Core 系统事件
    "CoreStartupPayload",
    "CoreShutdownPayload",
    "CoreErrorPayload",
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
    # v2 保留：Sticker→VTS 单向信号
    "StickerCommandPayload",
    # v2 语义域 — tool.result.*
    "ToolResultPayload",
]
