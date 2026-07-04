"""
事件历史记录器

独立的 EventBus 订阅者，将系统事件记录到 EventHistoryService。
与 Dashboard / EventBroadcaster 解耦 —— 即使 WebUI 未启用也始终运行。

记录的事件类型：
- input → message.received
- decision → decision.intent
- output → output.render
- 系统事件 → system.status / system.error
- 组件连接/断开 → collector.connected / collector.disconnected / decider.*
"""

from typing import TYPE_CHECKING, Any, Callable, Optional

from pydantic import BaseModel

from src.modules.events.event_history import EventRecord, EventHistoryService, infer_event_level
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    ConnectedPayload,
    DisconnectedPayload,
    IntentPayload,
    MessageReadyPayload,
)
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus

logger = get_logger("EventHistoryRecorder")

# 组件事件映射
_COMPONENT_EVENT_MAP: dict[str, str] = {
    CoreEvents.INPUT_CONNECTED: "collector.connected",
    CoreEvents.INPUT_DISCONNECTED: "collector.disconnected",
    CoreEvents.DECISION_CONNECTED: "decider.connected",
    CoreEvents.DECISION_DISCONNECTED: "decider.disconnected",
}


class EventHistoryRecorder:
    """事件历史记录器 —— 订阅 EventBus 并记录事件。

    独立于 Dashboard 运行，确保即使用户不使用 WebUI 也能记录事件。
    由 `main.py` 的 `create_app_components` 创建。
    """

    def __init__(self, event_bus: "EventBus", event_history: EventHistoryService) -> None:
        self.event_bus = event_bus
        self.event_history = event_history
        self._subscriptions: dict[str, Callable] = {}

    async def start(self) -> None:
        """订阅所有需要记录的事件。"""

        class _GenericPayload(BaseModel):
            event: str = ""
            message: str = ""
            data: Any = None

        self._subscribe(CoreEvents.INPUT_MESSAGE_RECEIVED, self._on_input_message, model_class=MessageReadyPayload)
        self._subscribe(CoreEvents.DECISION_INTENT_GENERATED, self._on_decision_intent, model_class=IntentPayload)
        self._subscribe(CoreEvents.OUTPUT_INTENT_DISPATCHED, self._on_output_intent, model_class=IntentPayload)
        self._subscribe(CoreEvents.CORE_STARTUP, self._on_core_event, model_class=_GenericPayload)
        self._subscribe(CoreEvents.CORE_SHUTDOWN, self._on_core_event, model_class=_GenericPayload)
        self._subscribe(CoreEvents.CORE_ERROR, self._on_core_error, model_class=_GenericPayload)

        component_model_map = {
            CoreEvents.INPUT_CONNECTED: ConnectedPayload,
            CoreEvents.INPUT_DISCONNECTED: DisconnectedPayload,
            CoreEvents.DECISION_CONNECTED: ConnectedPayload,
            CoreEvents.DECISION_DISCONNECTED: DisconnectedPayload,
        }
        for event_name, payload_class in component_model_map.items():
            self._subscribe(event_name, self._on_component_event, model_class=payload_class)

        logger.info(
            f"事件历史记录器已启动，订阅了 {len(self._subscriptions)} 个事件",
        )

    async def stop(self) -> None:
        """取消所有订阅。"""
        for event_name, handler in list(self._subscriptions.items()):
            try:
                self.event_bus.off(event_name, handler)
            except Exception as e:
                logger.warning(f"取消订阅 {event_name} 失败: {e}")
        self._subscriptions.clear()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _subscribe(self, event_name: str, handler: Callable, model_class: Any = None) -> None:
        """订阅单个事件。"""
        try:
            self.event_bus.on(event_name, handler, model_class=model_class)
            self._subscriptions[event_name] = handler
        except Exception as e:
            logger.error(f"订阅事件 {event_name} 失败: {e}")

    def _record(self, event: EventRecord) -> None:
        """记录事件（短路保护）。"""
        self.event_history.record(event)

    # ------------------------------------------------------------------
    # 事件处理器
    # ------------------------------------------------------------------

    async def _on_input_message(self, event_name: str, data: BaseModel, source: str) -> None:
        try:
            dict_data = data.model_dump()
            message = dict_data.get("message", {})
            summary = (message.get("text", "") if isinstance(message, dict) else str(message))[:200]
            self._record(
                EventRecord(
                    type="message.received",
                    level="info",
                    source=dict_data.get("source", source),
                    summary=summary,
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 input message 事件失败: {e}")

    async def _on_decision_intent(self, event_name: str, data: BaseModel, source: str) -> None:
        try:
            dict_data = data.model_dump()
            intent_data = dict_data.get("intent_data", {})
            summary = (intent_data.get("speech", "") if isinstance(intent_data, dict) else str(intent_data))[:200]
            self._record(
                EventRecord(
                    type="decision.intent",
                    level="info",
                    source=dict_data.get("name", source),
                    summary=summary,
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 decision intent 事件失败: {e}")

    async def _on_output_intent(self, event_name: str, data: BaseModel, source: str) -> None:
        try:
            dict_data = data.model_dump()
            intent_data = dict_data.get("intent_data", {})
            summary = (intent_data.get("speech", "") if isinstance(intent_data, dict) else str(intent_data))[:200]
            self._record(
                EventRecord(
                    type="output.render",
                    level="info",
                    source=dict_data.get("name", source),
                    summary=summary,
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 output intent 事件失败: {e}")

    async def _on_core_event(self, event_name: str, data: BaseModel, source: str) -> None:
        try:
            dict_data = {"event": event_name, "payload": self._safe_serialize(data)}
            self._record(
                EventRecord(
                    type="system.status",
                    level="info",
                    source="dashboard",
                    summary=str(dict_data.get("event", dict_data))[:200],
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 core event 失败: {e}")

    async def _on_core_error(self, event_name: str, data: BaseModel, source: str) -> None:
        try:
            dict_data = {
                "event": "error",
                "message": self._safe_serialize(data) or "Unknown error",
            }
            self._record(
                EventRecord(
                    type="system.error",
                    level="error",
                    source="dashboard",
                    summary=str(dict_data.get("message", ""))[:200],
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 core error 事件失败: {e}")

    async def _on_component_event(self, event_name: str, data: BaseModel, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            event_type = _COMPONENT_EVENT_MAP.get(event_name, "system.status")
            self._record(
                EventRecord(
                    type=event_type,
                    level=infer_event_level(event_type),
                    source=dict_data.get("name", "unknown"),
                    summary=f"{event_type}: {dict_data.get('name', '')}",
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 component event 失败: {e}")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _safe_serialize(self, data: Any) -> Optional[str]:
        if data is None:
            return None
        if isinstance(data, BaseModel):
            return str(data.model_dump())
        if isinstance(data, dict):
            return str(data)
        return str(data)
