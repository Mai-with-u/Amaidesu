"""
事件广播器

订阅 EventBus 事件并广播给 WebSocket 客户端。
"""

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

from pydantic import BaseModel

from src.modules.events.names import CoreEvents
from src.modules.events.payloads import (
    IntentPayload,
    MessageReadyPayload,
    ProviderConnectedPayload,
    ProviderDisconnectedPayload,
)
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.websocket.handler import WebSocketHandler
    from src.modules.events.event_bus import EventBus

logger = get_logger("EventBroadcaster")


class EventBroadcaster:
    """EventBus 事件广播器 - 将 EventBus 事件广播到 WebSocket 客户端"""

    # 事件类型映射：从 CoreEvents 常量到 WebSocket 事件类型
    EVENT_TYPE_MAP = {
        CoreEvents.INPUT_MESSAGE_READY: "message.received",
        CoreEvents.DECISION_INTENT_GENERATED: "decision.intent",
        CoreEvents.OUTPUT_INTENT_READY: "output.render",
    }

    # Provider 事件类型映射
    PROVIDER_EVENT_TYPE_MAP = {
        CoreEvents.INPUT_PROVIDER_CONNECTED: "provider.connected",
        CoreEvents.INPUT_PROVIDER_DISCONNECTED: "provider.disconnected",
        CoreEvents.DECISION_PROVIDER_CONNECTED: "provider.connected",
        CoreEvents.DECISION_PROVIDER_DISCONNECTED: "provider.disconnected",
        CoreEvents.OUTPUT_PROVIDER_CONNECTED: "provider.connected",
        CoreEvents.OUTPUT_PROVIDER_DISCONNECTED: "provider.disconnected",
    }

    def __init__(
        self,
        event_bus: "EventBus",
        ws_handler: "WebSocketHandler",
        subscribe_events: Optional[List[str]] = None,
    ):
        self.event_bus = event_bus
        self.ws_handler = ws_handler
        self.subscribe_events = subscribe_events or []
        self._subscribed_events: Set[str] = set()
        self._is_running = False

    async def start(self) -> None:
        """启动事件广播器"""
        if self._is_running:
            return

        self._is_running = True
        logger.info("事件广播器启动中...")

        # 订阅核心事件
        self._subscribe_core_events()

        # 订阅系统事件
        self._subscribe_system_events()

        logger.info(f"事件广播器已启动，订阅了 {len(self._subscribed_events)} 个事件")

    async def stop(self) -> None:
        """停止事件广播器"""
        self._is_running = False
        logger.info("事件广播器停止中...")

        # 取消所有订阅
        for event_name in self._subscribed_events:
            try:
                # 找到对应的处理器并取消订阅
                handler = self._get_handler_for_event(event_name)
                if handler:
                    self.event_bus.off(event_name, handler)
            except Exception as e:
                logger.error(f"取消订阅 {event_name} 失败: {e}")

        self._subscribed_events.clear()
        logger.info("事件广播器已停止")

    def _get_handler_for_event(self, event_name: str) -> Optional[Callable]:
        """获取事件对应的处理器"""
        handler_map = {
            CoreEvents.INPUT_MESSAGE_READY: self._on_input_message,
            CoreEvents.DECISION_INTENT_GENERATED: self._on_decision_intent,
            CoreEvents.OUTPUT_INTENT_READY: self._on_output_intent,
            CoreEvents.CORE_STARTUP: self._on_core_event,
            CoreEvents.CORE_SHUTDOWN: self._on_core_event,
            CoreEvents.CORE_ERROR: self._on_core_error,
        }
        # Provider 事件使用通用处理器
        if event_name in self.PROVIDER_EVENT_TYPE_MAP:
            return self._create_provider_handler(event_name)
        return handler_map.get(event_name)

    def _subscribe_core_events(self) -> None:
        """订阅核心数据流事件"""
        # 订阅 Input 事件 - 使用 MessageReadyPayload
        self._subscribe_event(
            CoreEvents.INPUT_MESSAGE_READY,
            self._on_input_message,
            model_class=MessageReadyPayload,
        )

        # 订阅 Decision 事件 - 使用 IntentPayload
        self._subscribe_event(
            CoreEvents.DECISION_INTENT_GENERATED,
            self._on_decision_intent,
            model_class=IntentPayload,
        )

        # 订阅 Output 事件 - 使用 IntentPayload
        self._subscribe_event(
            CoreEvents.OUTPUT_INTENT_READY,
            self._on_output_intent,
            model_class=IntentPayload,
        )

    def _subscribe_system_events(self) -> None:
        """订阅系统状态事件"""

        # 定义一个简单的通用 Payload 用于系统事件
        class GenericEventPayload(BaseModel):
            event: Optional[str] = None
            message: Optional[str] = None
            data: Any = None

        # 订阅核心生命周期事件
        system_events = [
            (CoreEvents.CORE_STARTUP, self._on_core_event),
            (CoreEvents.CORE_SHUTDOWN, self._on_core_event),
            (CoreEvents.CORE_ERROR, self._on_core_error),
        ]

        for event_name, handler in system_events:
            self._subscribe_event(event_name, handler, model_class=GenericEventPayload)

        # 订阅 Provider 状态事件
        provider_event_map = {
            CoreEvents.INPUT_PROVIDER_CONNECTED: ProviderConnectedPayload,
            CoreEvents.INPUT_PROVIDER_DISCONNECTED: ProviderDisconnectedPayload,
            CoreEvents.DECISION_PROVIDER_CONNECTED: ProviderConnectedPayload,
            CoreEvents.DECISION_PROVIDER_DISCONNECTED: ProviderDisconnectedPayload,
            CoreEvents.OUTPUT_PROVIDER_CONNECTED: ProviderConnectedPayload,
            CoreEvents.OUTPUT_PROVIDER_DISCONNECTED: ProviderDisconnectedPayload,
        }

        for event_name, payload_class in provider_event_map.items():
            handler = self._create_provider_handler(event_name)
            self._subscribe_event(event_name, handler, model_class=payload_class)

    def _create_provider_handler(self, target_event_name: str) -> Callable:
        """创建 Provider 事件处理器（闭包捕获事件名）"""

        async def handler(event_name: str, data: BaseModel, source: str) -> None:
            try:
                # data 现在是 ProviderConnectedPayload 或 ProviderDisconnectedPayload
                dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
                event_type = self.PROVIDER_EVENT_TYPE_MAP.get(target_event_name, "provider.connected")
                await self.ws_handler.broadcast(event_type, dict_data)
            except Exception as e:
                logger.error(f"广播 provider event 失败: {e}")

        return handler

    def _subscribe_event(self, event_name: str, handler: Callable, model_class: type[BaseModel]) -> None:
        """订阅单个事件"""
        try:
            self.event_bus.on(event_name, handler, model_class=model_class)
            self._subscribed_events.add(event_name)
            logger.debug(f"已订阅事件: {event_name}")
        except Exception as e:
            logger.error(f"订阅事件 {event_name} 失败: {e}")

    async def _on_input_message(self, event_name: str, data: MessageReadyPayload, source: str) -> None:
        """处理 Input 消息事件"""
        try:
            # data 现在是 MessageReadyPayload 类型，直接序列化
            dict_data = data.model_dump()
            await self.ws_handler.broadcast("message.received", dict_data)
        except Exception as e:
            logger.error(f"广播 input message 失败: {e}")

    async def _on_decision_intent(self, event_name: str, data: IntentPayload, source: str) -> None:
        """处理 Decision 意图事件"""
        try:
            # data 现在是 IntentPayload 类型，直接序列化
            dict_data = data.model_dump()
            await self.ws_handler.broadcast("decision.intent", dict_data)
        except Exception as e:
            logger.error(f"广播 decision intent 失败: {e}")

    async def _on_output_intent(self, event_name: str, data: IntentPayload, source: str) -> None:
        """处理 Output 意图事件"""
        try:
            # data 现在是 IntentPayload 类型，直接序列化
            dict_data = data.model_dump()
            await self.ws_handler.broadcast("output.render", dict_data)
        except Exception as e:
            logger.error(f"广播 output intent 失败: {e}")

    async def _on_core_event(self, event_name: str, data: Any, source: str) -> None:
        """处理核心事件"""
        try:
            dict_data = {"event": event_name, "payload": self._safe_serialize(data)}
            await self.ws_handler.broadcast("system.status", dict_data)
        except Exception as e:
            logger.error(f"广播 core event 失败: {e}")

    async def _on_core_error(self, event_name: str, data: Any, source: str) -> None:
        """处理核心错误事件"""
        try:
            dict_data = {
                "event": "error",
                "message": self._safe_serialize(data) or "Unknown error",
            }
            await self.ws_handler.broadcast("system.error", dict_data)
        except Exception as e:
            logger.error(f"广播 core error 失败: {e}")

    def _safe_serialize(self, data: Any) -> Optional[Dict[str, Any] | str]:
        """安全序列化数据"""
        if data is None:
            return None
        if isinstance(data, BaseModel):
            return data.model_dump()
        if isinstance(data, dict):
            return data
        return str(data)
