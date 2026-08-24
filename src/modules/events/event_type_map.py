"""
EventBus 事件名 → Dashboard 事件类型名 映射（单一事实源 / Wave 6）

EventHistoryRecorder（记录）与 EventBroadcaster（广播）共用此模块的
映射表与类型名常量，避免两侧各自维护映射/硬编码字符串导致漂移。

Wave 6 变更：
- 移除 decision.intent / output.render 类型常量（已删除的 Stage-glue 事件）
- 新增 room.message 类型常量（核心行为流）
"""

from src.modules.events.names import CoreEvents

# 组件连接/断开事件名 → 事件类型名（§1.46 语义域事件）
COMPONENT_EVENT_TYPE_MAP: dict[str, str] = {
    CoreEvents.GAME_MILESTONE: "game.milestone",
    CoreEvents.GAME_ATTENTION_REQUIRED: "game.attention_required",
    CoreEvents.GAME_ERROR: "game.error",
    CoreEvents.LIVE_STARTED: "live.started",
    CoreEvents.LIVE_ENDED: "live.ended",
}

# 事件类型名常量（广播/记录 handler 直接使用的字符串）
ROOM_MESSAGE_TYPE = "room.message"
MESSAGE_RECEIVED_TYPE = "message.received"  # 历史兼容
PLANNER_CHECKPOINT_TYPE = "planner.checkpoint"
AGENDA_UPDATE_TYPE = "agenda.update"
SYSTEM_STATUS_TYPE = "system.status"
SYSTEM_ERROR_TYPE = "system.error"

__all__ = [
    "COMPONENT_EVENT_TYPE_MAP",
    "ROOM_MESSAGE_TYPE",
    "MESSAGE_RECEIVED_TYPE",
    "PLANNER_CHECKPOINT_TYPE",
    "AGENDA_UPDATE_TYPE",
    "SYSTEM_STATUS_TYPE",
    "SYSTEM_ERROR_TYPE",
]
