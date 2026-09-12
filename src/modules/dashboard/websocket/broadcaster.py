"""
EventBus 事件广播器

订阅 EventBus 事件并广播给 WebSocket 客户端。

订阅的事件族：
- ``room.message.*``（danmaku / gift / super_chat / enter） → WS type ``"room.message"``
- ``planner.decision`` / ``streamer.stage`` / ``live.started`` / ``live.ended``
  → WS type = 事件名（直通）
- ``rundown.changed`` → WS type 来自 ``RUNDOWN_CHANGED_TYPE``
- ``streamer.speech`` → WS type ``"streamer.speech"``
- ``tool.result.#`` 通配 → WS type = 具体事件名（``tool.result.<tool_name>``），
  payload 为 ``ToolResultPayload.model_dump``
- ``tool.health.#`` 通配 → WS type = 具体事件名（``tool.health.<tool_name>``），
  payload 为 ``ToolHealthPayload.model_dump``；仅在熔断/恢复跃迁时发射
- ``core.startup`` / ``core.shutdown`` → WS type 来自 ``SYSTEM_STATUS_TYPE``
- ``core.error`` → WS type 来自 ``SYSTEM_ERROR_TYPE``
- 组件事件（``COMPONENT_EVENT_TYPE_MAP`` 涵盖的事件）按映射表输出
"""

import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

from pydantic import BaseModel

from src.modules.events.names import CoreEvents
from src.modules.events.event_type_map import (
    COMPONENT_EVENT_TYPE_MAP,
    ROOM_MESSAGE_TYPE,
    RUNDOWN_CHANGED_TYPE,
    SYSTEM_ERROR_TYPE,
    SYSTEM_STATUS_TYPE,
)
from src.modules.events.payloads import (
    CoreErrorPayload,
    CoreShutdownPayload,
    CoreStartupPayload,
    LiveEndedPayload,
    LiveStartedPayload,
    PlannerDecisionPayload,
    PlannerVerdictPayload,
    RoomMessagePayload,
    RundownChangedPayload,
    StreamerSpeechPayload,
    StreamerStagePayload,
    ToolHealthPayload,
    ToolResultPayload,
)
from src.modules.events.payloads.base import BasePayload
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.websocket.handler import WebSocketHandler
    from src.modules.events.event_bus import EventBus
    from src.modules.events.event_history import EventHistoryService

logger = get_logger("EventBroadcaster")


class EventBroadcaster:
    """EventBus 事件广播器 - 将 EventBus 事件广播到 WebSocket 客户端"""

    def __init__(
        self,
        event_bus: "EventBus",
        ws_handler: "WebSocketHandler",
        subscribe_events: Optional[List[str]] = None,
        event_history: Optional["EventHistoryService"] = None,
        history_push_limit: int = 100,
    ):
        self.event_bus = event_bus
        self.ws_handler = ws_handler
        self.subscribe_events = subscribe_events or []
        self.event_history = event_history
        self.history_push_limit = history_push_limit
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
            CoreEvents.ROOM_MESSAGE_DANMAKU: self._on_room_message,
            CoreEvents.ROOM_MESSAGE_GIFT: self._on_room_message,
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT: self._on_room_message,
            CoreEvents.ROOM_MESSAGE_ENTER: self._on_room_message,
            CoreEvents.PLANNER_DECISION: self._on_named_event,
            CoreEvents.PLANNER_VERDICT: self._on_named_event,
            CoreEvents.STREAMER_STAGE: self._on_named_event,
            CoreEvents.LIVE_STARTED: self._on_named_event,
            CoreEvents.LIVE_ENDED: self._on_named_event,
            CoreEvents.RUNDOWN_CHANGED: self._on_rundown_changed,
            CoreEvents.STREAMER_SPEECH: self._on_streamer_speech,
            CoreEvents.TOOL_RESULT_WILDCARD: self._on_tool_result,
            CoreEvents.TOOL_HEALTH_WILDCARD: self._on_tool_health,
            CoreEvents.CORE_STARTUP: self._on_core_event,
            CoreEvents.CORE_SHUTDOWN: self._on_core_event,
            CoreEvents.CORE_ERROR: self._on_core_error,
        }
        if event_name in COMPONENT_EVENT_TYPE_MAP:
            return self._create_component_handler(event_name)
        return handler_map.get(event_name)

    def _subscribe_core_events(self) -> None:
        self._subscribe_event(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            self._on_room_message,
            model_class=RoomMessagePayload,
        )
        for event_name in (
            CoreEvents.ROOM_MESSAGE_GIFT,
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
            CoreEvents.ROOM_MESSAGE_ENTER,
        ):
            self._subscribe_event(
                event_name,
                self._on_room_message,
                model_class=RoomMessagePayload,
            )
        self._subscribe_event(
            CoreEvents.STREAMER_SPEECH,
            self._on_streamer_speech,
            model_class=StreamerSpeechPayload,
        )
        self._subscribe_event(
            CoreEvents.TOOL_RESULT_WILDCARD,
            self._on_tool_result,
            model_class=ToolResultPayload,
        )
        self._subscribe_event(
            CoreEvents.TOOL_HEALTH_WILDCARD,
            self._on_tool_health,
            model_class=ToolHealthPayload,
        )
        # 决策可观测 + 场次生命周期（WS type 与事件名一致）
        self._subscribe_event(
            CoreEvents.PLANNER_DECISION,
            self._on_named_event,
            model_class=PlannerDecisionPayload,
        )
        self._subscribe_event(
            CoreEvents.PLANNER_VERDICT,
            self._on_named_event,
            model_class=PlannerVerdictPayload,
        )
        self._subscribe_event(
            CoreEvents.STREAMER_STAGE,
            self._on_named_event,
            model_class=StreamerStagePayload,
        )
        self._subscribe_event(
            CoreEvents.LIVE_STARTED,
            self._on_named_event,
            model_class=LiveStartedPayload,
        )
        self._subscribe_event(
            CoreEvents.LIVE_ENDED,
            self._on_named_event,
            model_class=LiveEndedPayload,
        )
        self._subscribe_event(
            CoreEvents.RUNDOWN_CHANGED,
            self._on_rundown_changed,
            model_class=RundownChangedPayload,
        )

    def _subscribe_system_events(self) -> None:
        for event_name, handler, payload_class in [
            (CoreEvents.CORE_STARTUP, self._on_core_event, CoreStartupPayload),
            (CoreEvents.CORE_SHUTDOWN, self._on_core_event, CoreShutdownPayload),
            (CoreEvents.CORE_ERROR, self._on_core_error, CoreErrorPayload),
        ]:
            self._subscribe_event(event_name, handler, model_class=payload_class)

    def _create_component_handler(self, target_event_name: str) -> Callable:
        async def handler(event_name: str, data: BasePayload, source: str) -> None:
            try:
                dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
                event_type = COMPONENT_EVENT_TYPE_MAP.get(target_event_name, "collector.connected")
                await self.ws_handler.broadcast(event_type, dict_data, message_id=data.id)
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

    async def _on_room_message(self, event_name: str, data: RoomMessagePayload, source: str) -> None:
        try:
            dict_data = data.model_dump()
            await self.ws_handler.broadcast(ROOM_MESSAGE_TYPE, dict_data, message_id=data.id)
        except Exception as e:
            logger.error(f"广播 room message 失败: {e}")

    async def _on_rundown_changed(self, event_name: str, data: RundownChangedPayload, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            await self.ws_handler.broadcast(RUNDOWN_CHANGED_TYPE, dict_data, message_id=data.id)
        except Exception as e:
            logger.error(f"广播 rundown changed 失败: {e}")

    async def _on_streamer_speech(self, event_name: str, data: StreamerSpeechPayload, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            await self.ws_handler.broadcast("streamer.speech", dict_data, message_id=data.id)
        except Exception as e:
            logger.error(f"广播 streamer speech 失败: {e}")

    async def _on_tool_result(self, event_name: str, data: ToolResultPayload, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            await self.ws_handler.broadcast(event_name, dict_data, message_id=data.id)
        except Exception as e:
            logger.error(f"广播 tool result 失败: {e}")

    async def _on_tool_health(self, event_name: str, data: ToolHealthPayload, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            await self.ws_handler.broadcast(event_name, dict_data, message_id=data.id)
        except Exception as e:
            logger.error(f"广播 tool health 失败: {e}")

    async def _on_named_event(self, event_name: str, data: BasePayload, source: str) -> None:
        """通用直通广播：WS type = 事件名（planner.decision / streamer.stage / live.*）。"""
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            await self.ws_handler.broadcast(event_name, dict_data, message_id=data.id)
        except Exception as e:
            logger.error(f"广播 {event_name} 失败: {e}")

    async def _on_core_event(self, event_name: str, data: Any, source: str) -> None:
        try:
            dict_data = {"event": event_name, "payload": self._safe_serialize(data)}
            await self.ws_handler.broadcast(
                SYSTEM_STATUS_TYPE,
                dict_data,
                message_id=data.id if isinstance(data, BasePayload) else None,
            )
        except Exception as e:
            logger.error(f"广播 core event 失败: {e}")

    async def _on_core_error(self, event_name: str, data: Any, source: str) -> None:
        try:
            dict_data = {
                "event": "error",
                "message": self._safe_serialize(data) or "Unknown error",
            }
            await self.ws_handler.broadcast(
                SYSTEM_ERROR_TYPE,
                dict_data,
                message_id=data.id if isinstance(data, BasePayload) else None,
            )
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
        recent = self.event_history.get_recent(self.history_push_limit)
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
