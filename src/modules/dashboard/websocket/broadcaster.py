"""
EventBus 事件广播器

订阅 EventBus 事件并广播给 WebSocket 客户端。

WS 类型规则：``type = 事件名``（精确直通）；唯一例外是 ``room.message.*``
折叠为 ``room.message``（前端多处按此聚合过滤）。

订阅清单：
- ``room.message.danmaku / gift / super_chat / enter`` → ``"room.message"``
- 决策可观测：``planner.decision`` / ``planner.verdict`` / ``streamer.stage``
- 场次生命周期：``live.started`` / ``live.ended``
- ``rundown.changed`` / ``streamer.speech``（直通）
- 游戏上报：``game.report`` / ``game.attention_required`` / ``game.error`` /
  ``game.milestone``（直通；游戏 Agent 的汇报/求助/异常/里程碑进直播时间线）
- ``core.startup`` / ``core.shutdown`` / ``core.error``（直通）
- ``tool.result.#`` / ``tool.health.#`` 通配 → type 为具体事件名
"""

from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set

from pydantic import BaseModel

from src.modules.events.event_type_map import ROOM_MESSAGE_TYPE
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    CoreErrorPayload,
    CoreShutdownPayload,
    CoreStartupPayload,
    GamePayload,
    LiveEndedPayload,
    LiveStartedPayload,
    PlannerDecisionPayload,
    PlannerVerdictPayload,
    RoomMessagePayload,
    RundownChangedPayload,
    StreamerSpeechPayload,
    StreamerStagePayload,
    TaskChangedPayload,
    ToolHealthPayload,
    ToolResultPayload,
)
from src.modules.events.payloads.base import BasePayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

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
    ) -> None:
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

        self._subscribe_events()

        logger.info(f"事件广播器已启动，订阅了 {len(self._subscribed_events)} 个事件")

    async def stop(self) -> None:
        """停止事件广播器"""
        self._is_running = False
        logger.info("事件广播器停止中...")

        for event_name in self._subscribed_events:
            try:
                handler = self._handler_for(event_name)
                if handler:
                    self.event_bus.off(event_name, handler)
            except Exception as e:
                logger.error(f"取消订阅 {event_name} 失败: {e}")

        self._subscribed_events.clear()
        logger.info("事件广播器已停止")

    def _handler_for(self, event_name: str) -> Optional[Callable]:
        """事件名 → 订阅时使用的 handler（与 ``_SUBSCRIPTIONS`` 一致）。"""
        if event_name in (
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            CoreEvents.ROOM_MESSAGE_GIFT,
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
            CoreEvents.ROOM_MESSAGE_ENTER,
        ):
            return self._on_room_message
        entry = self._SUBSCRIPTIONS.get(event_name)
        if entry is None:
            return None
        handler, _payload_class = entry
        return handler or self._on_named_event

    # 订阅清单：事件名 → (handler, payload 类型)。除 room.message 折叠外全部直通。
    _SUBSCRIPTIONS: Dict[str, tuple] = {
        CoreEvents.PLANNER_DECISION: (None, PlannerDecisionPayload),
        CoreEvents.PLANNER_VERDICT: (None, PlannerVerdictPayload),
        CoreEvents.STREAMER_STAGE: (None, StreamerStagePayload),
        CoreEvents.LIVE_STARTED: (None, LiveStartedPayload),
        CoreEvents.LIVE_ENDED: (None, LiveEndedPayload),
        CoreEvents.RUNDOWN_CHANGED: (None, RundownChangedPayload),
        CoreEvents.STREAMER_SPEECH: (None, StreamerSpeechPayload),
        CoreEvents.GAME_REPORT: (None, GamePayload),
        CoreEvents.GAME_ATTENTION_REQUIRED: (None, GamePayload),
        CoreEvents.GAME_ERROR: (None, GamePayload),
        CoreEvents.GAME_MILESTONE: (None, GamePayload),
        # 任务卡实时增量（进行中账本变化；已完结从事件环聚合）
        CoreEvents.TASK_CHANGED: (None, TaskChangedPayload),
        CoreEvents.TOOL_RESULT_WILDCARD: (None, ToolResultPayload),
        CoreEvents.TOOL_HEALTH_WILDCARD: (None, ToolHealthPayload),
        CoreEvents.CORE_STARTUP: (None, CoreStartupPayload),
        CoreEvents.CORE_SHUTDOWN: (None, CoreShutdownPayload),
        CoreEvents.CORE_ERROR: (None, CoreErrorPayload),
    }

    def _subscribe_events(self) -> None:
        """挂载全部订阅。handler 为 None 的条目统一走直通广播。"""
        for event_name, (handler, payload_class) in self._SUBSCRIPTIONS.items():
            self._subscribe_event(event_name, handler or self._on_named_event, payload_class)

        for event_name in (
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            CoreEvents.ROOM_MESSAGE_GIFT,
            CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
            CoreEvents.ROOM_MESSAGE_ENTER,
        ):
            self._subscribe_event(event_name, self._on_room_message, RoomMessagePayload)

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

    async def _on_named_event(self, event_name: str, data: BasePayload, source: str) -> None:
        """通用直通广播：WS type = 事件名。"""
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            await self.ws_handler.broadcast(event_name, dict_data, message_id=data.id)
        except Exception as e:
            logger.error(f"广播 {event_name} 失败: {e}")

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
            timestamp_ms=now_ms(),
            data={"events": [e.model_dump() for e in recent]},
        )
        try:
            await self.ws_handler.send_to_client(client_id, message)
        except Exception as e:
            logger.debug(f"推送事件历史到客户端 {client_id} 失败: {e}")
