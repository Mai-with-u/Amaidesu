"""
事件历史记录器（语义域事件）

独立的 EventBus 订阅者，将系统事件记录到 EventHistoryService。
与 Dashboard / EventBroadcaster 解耦 —— 即使 WebUI 未启用也始终运行。
"""

from typing import TYPE_CHECKING, Any, Callable, Optional

from pydantic import BaseModel

from src.modules.events.event_history import EventRecord, EventHistoryService
from src.modules.events.event_type_map import (
    ROOM_MESSAGE_TYPE,
    SYSTEM_ERROR_TYPE,
    SYSTEM_STATUS_TYPE,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    AgendaPayload,
    CheckpointPayload,
    CoreErrorPayload,
    CoreShutdownPayload,
    CoreStartupPayload,
    GamePayload,
    LiveEndedPayload,
    LiveStartedPayload,
    PlannerDecisionPayload,
    RoomMessagePayload,
    StreamerStagePayload,
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
        self._subscribe(CoreEvents.PLANNER_CHECKPOINT, self._on_core_event, model_class=CheckpointPayload)
        self._subscribe(CoreEvents.AGENDA_UPDATE, self._on_core_event, model_class=AgendaPayload)

        # 决策可观测 + 场次生命周期（type=事件名独立记录；观察器按场回看的数据源）
        self._subscribe(CoreEvents.PLANNER_DECISION, self._on_named_event, model_class=PlannerDecisionPayload)
        self._subscribe(CoreEvents.STREAMER_STAGE, self._on_named_event, model_class=StreamerStagePayload)
        self._subscribe(CoreEvents.LIVE_STARTED, self._on_named_event, model_class=LiveStartedPayload)
        self._subscribe(CoreEvents.LIVE_ENDED, self._on_named_event, model_class=LiveEndedPayload)

        component_model_map = {
            CoreEvents.GAME_MILESTONE: GamePayload,
            CoreEvents.GAME_ATTENTION_REQUIRED: GamePayload,
            CoreEvents.GAME_ERROR: GamePayload,
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

    @staticmethod
    def _payload_timestamp_ms(data: Any) -> Optional[int]:
        """从载荷提取毫秒时刻；载荷未携带时返回 None（落库时退回记录时刻换算）。"""
        value = getattr(data, "timestamp_ms", None)
        return int(value) if isinstance(value, (int, float)) else None

    async def _on_room_message(self, event_name: str, data: BasePayload, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            content = dict_data.get("content", "") or ""
            summary = f"[{dict_data.get('message_type', 'unknown')}] {content}"[:200]
            self._record(
                EventRecord(
                    id=data.id if hasattr(data, "id") else "",
                    type=ROOM_MESSAGE_TYPE,
                    event_name=event_name,
                    timestamp_ms=self._payload_timestamp_ms(data),
                    level="info",
                    source=str(dict_data.get("live_session_id", source) or source),
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
                    event_name=event_name,
                    timestamp_ms=self._payload_timestamp_ms(data),
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
                    event_name=event_name,
                    timestamp_ms=self._payload_timestamp_ms(data),
                    level="error",
                    source="dashboard",
                    summary=str(dict_data.get("message", ""))[:200],
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 core error 事件失败: {e}")

    async def _on_named_event(self, event_name: str, data: BasePayload, source: str) -> None:
        """通用命名事件记录：type = 事件名（planner.decision / streamer.stage / live.*）。

        场次主键进 ``source`` 字段，供按场过滤回看。
        """
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            summary = str(dict_data.get("detail") or dict_data.get("title") or dict_data.get("reason") or event_name)
            self._record(
                EventRecord(
                    id=data.id if hasattr(data, "id") else "",
                    type=event_name,
                    event_name=event_name,
                    timestamp_ms=self._payload_timestamp_ms(data),
                    level="info",
                    source=str(dict_data.get("live_session_id", "") or source),
                    summary=summary[:200],
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 {event_name} 事件失败: {e}")

    async def _on_component_event(self, event_name: str, data: BasePayload, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            self._record(
                EventRecord(
                    id=data.id if hasattr(data, "id") else "",
                    type=event_name,
                    event_name=event_name,
                    timestamp_ms=self._payload_timestamp_ms(data),
                    level="info",
                    source=str(dict_data.get("live_session_id", "") or source),
                    summary=f"{event_name}: {dict_data.get('message', '')}"[:200],
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
