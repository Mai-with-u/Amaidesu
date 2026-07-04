"""
EventBus 事件广播器

订阅 EventBus 事件并广播给 WebSocket 客户端。
注意：事件记录已由 EventHistoryRecorder 独立处理（system 级，与 Dashboard 解耦）。
"""

import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

from pydantic import BaseModel

from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    IntentPayload,
    MessageReadyPayload,
    ConnectedPayload,
    DisconnectedPayload,
)
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.websocket.handler import WebSocketHandler
    from src.modules.events.event_bus import EventBus
    from src.modules.events.event_history import EventHistoryService

logger = get_logger("EventBroadcaster")


class EventBroadcaster:
    """EventBus 事件广播器 - 将 EventBus 事件广播到 WebSocket 客户端"""

    EVENT_TYPE_MAP = {
        CoreEvents.INPUT_MESSAGE_RECEIVED: "message.received",
        CoreEvents.DECISION_INTENT_GENERATED: "decision.intent",
        CoreEvents.OUTPUT_INTENT_DISPATCHED: "output.render",
    }

    COMPONENT_EVENT_TYPE_MAP = {
        CoreEvents.INPUT_CONNECTED: "collector.connected",
        CoreEvents.INPUT_DISCONNECTED: "collector.disconnected",
        CoreEvents.DECISION_CONNECTED: "decider.connected",
        CoreEvents.DECISION_DISCONNECTED: "decider.disconnected",
    }

    def __init__(
        self,
        event_bus: "EventBus",
        ws_handler: "WebSocketHandler",
        subscribe_events: Optional[List[str]] = None,
        event_history: Optional["EventHistoryService"] = None,
    ):
        self.event_bus = event_bus
        self.ws_handler = ws_handler
        self.subscribe_events = subscribe_events or []
        self.event_history = event_history  # 仅用于 push_history_to_client
        self._subscribed_events: Set[str] = set()
        self._is_running = False

    async def start(self) -> None:
        """启动事件广播器"""
        if self._is_running:
            return

        self._is_running = True
        logger.info("事件广播器启动中...")

        self._subscribe_core_events()
        self._subscribe_system_events()

        logger.info(f"事件广播器已启动，订阅了 {len(self._subscribed_events)} 个事件")

    async def stop(self) -> None:
        """停止事件广播器"""
        self._is_running = False
        logger.info("事件广播器停止中...")

        for event_name in self._subscribed_events:
            try:
                handler = self._get_handler_for_event(event_name)
                if handler:
                    self.event_bus.off(event_name, handler)
            except Exception as e:
                logger.error(f"取消订阅 {event_name} 失败: {e}")

        self._subscribed_events.clear()
        logger.info("事件广播器已停止")

    def _get_handler_for_event(self, event_name: str) -> Optional[Callable]:
        handler_map = {
            CoreEvents.INPUT_MESSAGE_RECEIVED: self._on_input_message,
            CoreEvents.DECISION_INTENT_GENERATED: self._on_decision_intent,
            CoreEvents.OUTPUT_INTENT_DISPATCHED: self._on_output_intent,
            CoreEvents.CORE_STARTUP: self._on_core_event,
            CoreEvents.CORE_SHUTDOWN: self._on_core_event,
            CoreEvents.CORE_ERROR: self._on_core_error,
        }
        if event_name in self.COMPONENT_EVENT_TYPE_MAP:
            return self._create_component_handler(event_name)
        return handler_map.get(event_name)

    def _subscribe_core_events(self) -> None:
        self._subscribe_event(
            CoreEvents.INPUT_MESSAGE_RECEIVED, self._on_input_message, model_class=MessageReadyPayload
        )
        self._subscribe_event(CoreEvents.DECISION_INTENT_GENERATED, self._on_decision_intent, model_class=IntentPayload)
        self._subscribe_event(CoreEvents.OUTPUT_INTENT_DISPATCHED, self._on_output_intent, model_class=IntentPayload)

    def _subscribe_system_events(self) -> None:
        class GenericEventPayload(BaseModel):
            event: Optional[str] = None
            message: Optional[str] = None
            data: Any = None

        for event_name, handler in [
            (CoreEvents.CORE_STARTUP, self._on_core_event),
            (CoreEvents.CORE_SHUTDOWN, self._on_core_event),
            (CoreEvents.CORE_ERROR, self._on_core_error),
        ]:
            self._subscribe_event(event_name, handler, model_class=GenericEventPayload)

        component_map = {
            CoreEvents.INPUT_CONNECTED: ConnectedPayload,
            CoreEvents.INPUT_DISCONNECTED: DisconnectedPayload,
            CoreEvents.DECISION_CONNECTED: ConnectedPayload,
            CoreEvents.DECISION_DISCONNECTED: DisconnectedPayload,
        }
        for event_name, payload_class in component_map.items():
            self._subscribe_event(event_name, self._create_component_handler(event_name), model_class=payload_class)

    def _create_component_handler(self, target_event_name: str) -> Callable:
        async def handler(event_name: str, data: BaseModel, source: str) -> None:
            try:
                dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
                event_type = self.COMPONENT_EVENT_TYPE_MAP.get(target_event_name, "collector.connected")
                await self.ws_handler.broadcast(event_type, dict_data)
            except Exception as e:
                logger.error(f"广播 component event 失败: {e}")

        return handler

    def _subscribe_event(self, event_name: str, handler: Callable, model_class: type[BaseModel]) -> None:
        try:
            self.event_bus.on(event_name, handler, model_class=model_class)
            self._subscribed_events.add(event_name)
            logger.debug(f"已订阅事件: {event_name}")
        except Exception as e:
            logger.error(f"订阅事件 {event_name} 失败: {e}")

    async def _on_input_message(self, event_name: str, data: MessageReadyPayload, source: str) -> None:
        try:
            dict_data = data.model_dump()
            await self.ws_handler.broadcast("message.received", dict_data)
        except Exception as e:
            logger.error(f"广播 input message 失败: {e}")

    async def _on_decision_intent(self, event_name: str, data: IntentPayload, source: str) -> None:
        try:
            dict_data = data.model_dump()
            await self.ws_handler.broadcast("decision.intent", dict_data)
        except Exception as e:
            logger.error(f"广播 decision intent 失败: {e}")

    async def _on_output_intent(self, event_name: str, data: IntentPayload, source: str) -> None:
        try:
            dict_data = data.model_dump()
            await self.ws_handler.broadcast("output.render", dict_data)
        except Exception as e:
            logger.error(f"广播 output intent 失败: {e}")

    async def _on_core_event(self, event_name: str, data: Any, source: str) -> None:
        try:
            dict_data = {"event": event_name, "payload": self._safe_serialize(data)}
            await self.ws_handler.broadcast("system.status", dict_data)
        except Exception as e:
            logger.error(f"广播 core event 失败: {e}")

    async def _on_core_error(self, event_name: str, data: Any, source: str) -> None:
        try:
            dict_data = {
                "event": "error",
                "message": self._safe_serialize(data) or "Unknown error",
            }
            await self.ws_handler.broadcast("system.error", dict_data)
        except Exception as e:
            logger.error(f"广播 core error 失败: {e}")

    def _safe_serialize(self, data: Any) -> Optional[Dict[str, Any] | str]:
        if data is None:
            return None
        if isinstance(data, BaseModel):
            return data.model_dump()
        if isinstance(data, dict):
            return data
        return str(data)

    async def push_history_to_client(self, client_id: str) -> None:
        """向新连接的客户端推送最近的事件历史（匹配 LogStreamer 模式）。"""
        if not self.event_history:
            return
        recent = self.event_history.get_recent(100)
        if not recent:
            return
        from src.modules.dashboard.schemas.event import WebSocketMessage

        message = WebSocketMessage(
            type="events.history",
            timestamp=time.time(),
            data={"events": [e.model_dump() for e in recent]},
        )
        try:
            await self.ws_handler._send_to_client(client_id, message)
        except Exception as e:
            logger.debug(f"推送事件历史到客户端 {client_id} 失败: {e}")
