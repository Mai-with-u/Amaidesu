"""
事件历史记录器（Wave 6 / §1.46 语义域事件）

独立的 EventBus 订阅者，将系统事件记录到 EventHistoryService。
与 Dashboard / EventBroadcaster 解耦 —— 即使 WebUI 未启用也始终运行。

Wave 6 变更：
- 移除 decision.intent.* / output.intent.* 订阅（已删除的事件，Stage-glue 胶水）
- 移除 INPUT_MESSAGE_RECEIVED 订阅（Wave 6 输入事件名 → room.message.*）
- 新增 room.message.* 订阅（核心行为流）
"""

from typing import TYPE_CHECKING, Any, Callable, Optional

from pydantic import BaseModel

from src.modules.events.event_history import EventRecord, EventHistoryService
from src.modules.events.event_type_map import (
    COMPONENT_EVENT_TYPE_MAP,
    ROOM_MESSAGE_TYPE,
    SYSTEM_ERROR_TYPE,
    SYSTEM_STATUS_TYPE,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    ConnectionEventPayload,
    CoreErrorPayload,
    CoreShutdownPayload,
    CoreStartupPayload,
    RoomMessagePayload,
)
from src.modules.events.payloads.base import BasePayload
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus

logger = get_logger("EventHistoryRecorder")


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

        self._subscribe(CoreEvents.ROOM_MESSAGE_DANMAKU, self._on_room_message, model_class=RoomMessagePayload)
        self._subscribe(CoreEvents.ROOM_MESSAGE_GIFT, self._on_room_message, model_class=RoomMessagePayload)
        self._subscribe(
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
            self._on_room_message,
            model_class=RoomMessagePayload,
        )
        self._subscribe(CoreEvents.ROOM_MESSAGE_ENTER, self._on_room_message, model_class=RoomMessagePayload)
        self._subscribe(CoreEvents.CORE_STARTUP, self._on_core_event, model_class=CoreStartupPayload)
        self._subscribe(CoreEvents.CORE_SHUTDOWN, self._on_core_event, model_class=CoreShutdownPayload)
        self._subscribe(CoreEvents.CORE_ERROR, self._on_core_error, model_class=CoreErrorPayload)
        self._subscribe(CoreEvents.PLANNER_CHECKPOINT, self._on_core_event, model_class=None)
        self._subscribe(CoreEvents.AGENDA_UPDATE, self._on_core_event, model_class=None)

        component_model_map = {
            CoreEvents.GAME_MILESTONE: ConnectionEventPayload,
            CoreEvents.GAME_ATTENTION_REQUIRED: ConnectionEventPayload,
            CoreEvents.GAME_ERROR: ConnectionEventPayload,
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

    def _subscribe(self, event_name: str, handler: Callable, model_class: Any = None) -> None:
        try:
            if model_class is None:
                self.event_bus.on(event_name, handler, model_class=BasePayload)
            else:
                self.event_bus.on(event_name, handler, model_class=model_class)
            self._subscriptions[event_name] = handler
        except Exception as e:
            logger.error(f"订阅事件 {event_name} 失败: {e}")

    def _record(self, event: EventRecord) -> None:
        self.event_history.record(event)

    async def _on_room_message(self, event_name: str, data: BasePayload, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            content = dict_data.get("content", "") or ""
            summary = f"[{dict_data.get('message_type', 'unknown')}] {content}"[:200]
            self._record(
                EventRecord(
                    id=data.id if hasattr(data, "id") else "",
                    type=ROOM_MESSAGE_TYPE,
                    level="info",
                    source=dict_data.get("live_session_id", source),
                    summary=summary,
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 room message 事件失败: {e}")

    async def _on_core_event(self, event_name: str, data: BasePayload, source: str) -> None:
        try:
            dict_data = {"event": event_name, "payload": self._safe_serialize(data)}
            self._record(
                EventRecord(
                    id=data.id if hasattr(data, "id") else "",
                    type=SYSTEM_STATUS_TYPE,
                    level="info",
                    source="dashboard",
                    summary=str(dict_data.get("event", dict_data))[:200],
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 core event 失败: {e}")

    async def _on_core_error(self, event_name: str, data: BasePayload, source: str) -> None:
        try:
            dict_data = {
                "event": "error",
                "message": self._safe_serialize(data) or "Unknown error",
            }
            self._record(
                EventRecord(
                    id=data.id if hasattr(data, "id") else "",
                    type=SYSTEM_ERROR_TYPE,
                    level="error",
                    source="dashboard",
                    summary=str(dict_data.get("message", ""))[:200],
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 core error 事件失败: {e}")

    async def _on_component_event(self, event_name: str, data: BasePayload, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            event_type = COMPONENT_EVENT_TYPE_MAP.get(event_name, SYSTEM_STATUS_TYPE)
            self._record(
                EventRecord(
                    id=data.id if hasattr(data, "id") else "",
                    type=event_type,
                    level="info",
                    source=dict_data.get("name", "unknown"),
                    summary=f"{event_type}: {dict_data.get('name', '')}",
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 component event 失败: {e}")

    def _safe_serialize(self, data: Any) -> Optional[str]:
        if data is None:
            return None
        if isinstance(data, BaseModel):
            return str(data.model_dump())
        if isinstance(data, dict):
            return str(data)
        return str(data)
