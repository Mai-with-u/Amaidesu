"""
EventBus 事件名 → Dashboard 事件类型名 映射（单一事实源 / Wave 6+8）

EventHistoryRecorder（记录）与 EventBroadcaster（广播）共用此模块的
映射表与类型名常量，避免两侧各自维护映射/硬编码字符串导致漂移。

变更记录：
- Wave 6: 移除 decision.intent / output.render 类型常量（已删除的 Stage-glue 事件），
  新增 room.message / planner.checkpoint / agenda.update / system.* 常量。
- Wave 8: 移除 MESSAGE_RECEIVED_TYPE（"message.received" 历史兼容名）—— v2 中
  EventHistoryRecorder 统一写入 ROOM_MESSAGE_TYPE，零引用此常量（grep 验证）。
"""

from src.modules.events.names import CoreEvents

# 组件事件名 → 事件类型名（§1.46 语义域事件）
COMPONENT_EVENT_TYPE_MAP: dict[str, str] = {
    CoreEvents.GAME_MILESTONE: "game.milestone",
    CoreEvents.GAME_ATTENTION_REQUIRED: "game.attention_required",
    CoreEvents.GAME_ERROR: "game.error",
    CoreEvents.LIVE_STARTED: "live.started",
    CoreEvents.LIVE_ENDED: "live.ended",
}

# 事件类型名常量（广播/记录 handler 直接使用的字符串）
ROOM_MESSAGE_TYPE = "room.message"
PLANNER_CHECKPOINT_TYPE = "planner.checkpoint"
AGENDA_UPDATE_TYPE = "agenda.update"
SYSTEM_STATUS_TYPE = "system.status"
SYSTEM_ERROR_TYPE = "system.error"

__all__ = [
    "COMPONENT_EVENT_TYPE_MAP",
    "ROOM_MESSAGE_TYPE",
    "PLANNER_CHECKPOINT_TYPE",
    "AGENDA_UPDATE_TYPE",
    "SYSTEM_STATUS_TYPE",
    "SYSTEM_ERROR_TYPE",
]
