"""
EventBus 事件名 → Dashboard 事件类型名 映射（单一事实源）

EventHistoryRecorder（记录）与 EventBroadcaster（广播）共用此模块的
映射表与类型名常量，避免两侧各自维护映射/硬编码字符串导致漂移。
"""

from src.modules.events.names import CoreEvents

# 组件连接/断开事件名 → 事件类型名
COMPONENT_EVENT_TYPE_MAP: dict[str, str] = {
    CoreEvents.INPUT_CONNECTED: "collector.connected",
    CoreEvents.INPUT_DISCONNECTED: "collector.disconnected",
    CoreEvents.DECISION_CONNECTED: "decider.connected",
    CoreEvents.DECISION_DISCONNECTED: "decider.disconnected",
}

# 事件类型名常量（广播/记录 handler 直接使用的字符串）
MESSAGE_RECEIVED_TYPE = "message.received"
DECISION_INTENT_TYPE = "decision.intent"
OUTPUT_RENDER_TYPE = "output.render"
SYSTEM_STATUS_TYPE = "system.status"
SYSTEM_ERROR_TYPE = "system.error"

__all__ = [
    "COMPONENT_EVENT_TYPE_MAP",
    "MESSAGE_RECEIVED_TYPE",
    "DECISION_INTENT_TYPE",
    "OUTPUT_RENDER_TYPE",
    "SYSTEM_STATUS_TYPE",
    "SYSTEM_ERROR_TYPE",
]
