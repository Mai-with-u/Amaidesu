"""
弹幕小部件服务

订阅输入事件，管理消息队列，并通过 WebSocket 广播给前端。
用于 Warudo 等虚拟形象软件的网页道具场景。
"""

from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, List, Optional

from src.modules.dashboard.widget.models import (
    MessageType,
    DanmakuWidgetConfig,
    DanmakuWidgetMessage,
    SubtitleWidgetConfig,
    SubtitleWidgetMessage,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.events.payloads.input import MessageReadyPayload
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus


class DanmakuWidgetService:
    """
    弹幕小部件服务

    订阅输入域的消息事件，转换为 DanmakuWidgetMessage 并广播到前端。
    用于 Warudo 等虚拟形象软件的网页道具场景。
    """

    def __init__(
        self,
        event_bus: "EventBus",
        config: Optional[DanmakuWidgetConfig] = None,
        subtitle_config: Optional[SubtitleWidgetConfig] = None,
    ):
        self.event_bus = event_bus
        self.config = config or DanmakuWidgetConfig()
        self.subtitle_config = subtitle_config or SubtitleWidgetConfig()
        self.logger = get_logger("DanmakuWidgetService")

        self.messages: Deque[DanmakuWidgetMessage] = deque(maxlen=self.config.max_messages)
        self.subtitle_messages: Deque[SubtitleWidgetMessage] = deque(maxlen=self.subtitle_config.max_messages)

        self._danmaku_callback: Optional[Callable[[dict], Any]] = None
        self._subtitle_callback: Optional[Callable[[dict], Any]] = None
        self._broadcast_callback: Optional[Callable[[dict], Any]] = None

        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def set_danmaku_callback(self, callback: Callable[[dict], Any]) -> None:
        self._danmaku_callback = callback

    def set_subtitle_callback(self, callback: Callable[[dict], Any]) -> None:
        self._subtitle_callback = callback

    def set_broadcast_callback(self, callback: Callable[[dict], Any]) -> None:
        self._broadcast_callback = callback

    async def start(self) -> None:
        if self._is_running:
            self.logger.warning("DanmakuWidgetService 已经在运行中")
            return

        self.event_bus.on(
            CoreEvents.INPUT_MESSAGE_READY,
            self._on_input_message,
            model_class=MessageReadyPayload,
        )

        self.event_bus.on(
            CoreEvents.DECISION_INTENT_GENERATED,
            self._on_decision_intent,
            model_class=IntentPayload,
        )

        self._is_running = True
        self.logger.info(f"DanmakuWidgetService 已启动 (max_messages={self.config.max_messages})")

    async def stop(self) -> None:
        if not self._is_running:
            return

        self.event_bus.off(CoreEvents.INPUT_MESSAGE_READY, self._on_input_message)
        self.event_bus.off(CoreEvents.DECISION_INTENT_GENERATED, self._on_decision_intent)

        self._is_running = False
        self.logger.info("DanmakuWidgetService 已停止")

    async def _on_input_message(
        self,
        event_name: str,
        payload: MessageReadyPayload,
        source: str,
    ) -> None:
        try:
            msg_dict = payload.message
            self.logger.debug(
                f"收到消息: {msg_dict.get('text', '')[:50]}, user_nickname={msg_dict.get('user_nickname')}, username={msg_dict.get('metadata', {}).get('username')}"
            )
            widget_msg = self._convert_dict_to_widget(msg_dict)
            if widget_msg is None:
                return

            if not self._should_display(widget_msg):
                return

            self.messages.append(widget_msg)

            await self._broadcast_new_message(widget_msg)

        except Exception as e:
            self.logger.error(f"处理输入消息失败: {e}", exc_info=True)

    async def _on_decision_intent(
        self,
        event_name: str,
        payload: IntentPayload,
        source: str,
    ) -> None:
        try:
            intent_data = payload.intent_data
            response_text = intent_data.get("response_text", "")
            if not response_text:
                return

            if not self.config.show_subtitle:
                return

            await self._broadcast_subtitle(response_text)
            self.logger.debug(f"广播字幕: {response_text[:30]}...")

        except Exception as e:
            self.logger.error(f"处理决策意图失败: {e}", exc_info=True)

    async def _broadcast_subtitle(self, text: str) -> None:
        if not self.subtitle_config.enabled:
            return

        subtitle_msg = SubtitleWidgetMessage(
            text=text,
            duration_ms=self.subtitle_config.auto_hide_after_ms,
        )
        self.subtitle_messages.append(subtitle_msg)

        data = {
            "type": "subtitle",
            "text": text,
            "duration_ms": subtitle_msg.duration_ms,
            "font_size": self.subtitle_config.font_size,
            "font_color": self.subtitle_config.font_color,
            "background_color": self.subtitle_config.background_color,
            "border_color": self.subtitle_config.border_color,
            "position": self.subtitle_config.position,
        }

        if self._subtitle_callback:
            try:
                await self._subtitle_callback(data)
            except Exception as e:
                self.logger.error(f"广播字幕到subtitle端失败: {e}", exc_info=True)

        if self._broadcast_callback:
            try:
                await self._broadcast_callback(data)
            except Exception as e:
                self.logger.error(f"广播字幕失败: {e}", exc_info=True)

    def _convert_dict_to_widget(self, msg_dict: Dict[str, Any]) -> Optional[DanmakuWidgetMessage]:
        try:
            metadata = msg_dict.get("metadata", {})
            user_name = (
                metadata.get("username") or msg_dict.get("user_nickname") or msg_dict.get("nickname") or "匿名用户"
            )
            user_id = metadata.get("user_id") or msg_dict.get("user_id") or ""
            platform = msg_dict.get("platform") or "unknown"
            room_id = msg_dict.get("room_id")
            importance = msg_dict.get("importance", 0.5)
            timestamp = msg_dict.get("timestamp")
            text = msg_dict.get("text", "")
            data_type = msg_dict.get("data_type", "text")

            dt_timestamp = datetime.fromtimestamp(timestamp) if timestamp else datetime.now()

            raw = msg_dict.get("raw") or msg_dict.get("raw_data") or {}

            if data_type == "text":
                return DanmakuWidgetMessage(
                    user_name=user_name,
                    user_id=user_id,
                    content=text,
                    message_type=MessageType.DANMAKU,
                    timestamp=dt_timestamp,
                    importance=importance,
                    platform=platform,
                    room_id=room_id,
                )

            elif data_type == "gift":
                gift_name = raw.get("gift_name")
                gift_count = raw.get("gift_num")
                gift_price = raw.get("price")

                return DanmakuWidgetMessage(
                    user_name=user_name,
                    user_id=user_id,
                    content=text,
                    message_type=MessageType.GIFT,
                    timestamp=dt_timestamp,
                    importance=importance,
                    gift_name=gift_name,
                    gift_count=gift_count,
                    gift_price=float(gift_price) if gift_price else None,
                    platform=platform,
                    room_id=room_id,
                )

            elif data_type == "super_chat":
                sc_price = raw.get("rmb")
                sc_message = raw.get("message")

                return DanmakuWidgetMessage(
                    user_name=user_name,
                    user_id=user_id,
                    content=text,
                    message_type=MessageType.SUPER_CHAT,
                    timestamp=dt_timestamp,
                    importance=importance,
                    sc_price=float(sc_price) if sc_price else None,
                    sc_message=sc_message,
                    platform=platform,
                    room_id=room_id,
                )

            elif data_type == "guard":
                guard_level = raw.get("guard_level")

                return DanmakuWidgetMessage(
                    user_name=user_name,
                    user_id=user_id,
                    content=text,
                    message_type=MessageType.GUARD,
                    timestamp=dt_timestamp,
                    importance=importance,
                    guard_level=guard_level,
                    platform=platform,
                    room_id=room_id,
                )

            elif data_type == "enter":
                return DanmakuWidgetMessage(
                    user_name=user_name,
                    user_id=user_id,
                    content=text,
                    message_type=MessageType.ENTER,
                    timestamp=dt_timestamp,
                    importance=importance,
                    platform=platform,
                    room_id=room_id,
                )

            else:
                self.logger.debug(f"未知消息类型: {data_type}")
                return DanmakuWidgetMessage(
                    user_name=user_name,
                    user_id=user_id,
                    content=text,
                    message_type=MessageType.DANMAKU,
                    timestamp=dt_timestamp,
                    importance=importance,
                    platform=platform,
                    room_id=room_id,
                )

        except Exception as e:
            self.logger.error(f"转换消息失败: {e}", exc_info=True)
            return None

    def _should_display(self, msg: DanmakuWidgetMessage) -> bool:
        if msg.importance < self.config.min_importance:
            return False

        if msg.message_type == MessageType.DANMAKU and not self.config.show_danmaku:
            return False
        if msg.message_type == MessageType.GIFT and not self.config.show_gift:
            return False
        if msg.message_type == MessageType.SUPER_CHAT and not self.config.show_super_chat:
            return False
        if msg.message_type == MessageType.GUARD and not self.config.show_guard:
            return False
        if msg.message_type == MessageType.ENTER and not self.config.show_enter:
            return False
        if msg.message_type == MessageType.REPLY and not self.config.show_reply:
            return False

        return True

    async def _broadcast_new_message(self, msg: DanmakuWidgetMessage) -> None:
        data = {
            "type": "new_message",
            "message": msg.model_dump(mode="json"),
        }

        if self._danmaku_callback:
            try:
                await self._danmaku_callback(data)
            except Exception as e:
                self.logger.error(f"广播消息到danmaku端失败: {e}", exc_info=True)

        if self._broadcast_callback:
            try:
                await self._broadcast_callback(data)
            except Exception as e:
                self.logger.error(f"广播消息失败: {e}", exc_info=True)

    def get_recent_messages(self, count: int = 15) -> List[dict]:
        messages = list(self.messages)[-count:]
        return [msg.model_dump(mode="json") for msg in messages]

    def get_recent_subtitles(self, count: int = 5) -> List[dict]:
        subtitles = list(self.subtitle_messages)[-count:]
        return [msg.model_dump(mode="json") for msg in subtitles]

    def clear_messages(self) -> None:
        self.messages.clear()
        self.logger.info("消息队列已清空")

    def clear_subtitles(self) -> None:
        self.subtitle_messages.clear()
        self.logger.info("字幕队列已清空")

    def get_stats(self) -> dict:
        return {
            "is_running": self._is_running,
            "message_count": len(self.messages),
            "max_messages": self.config.max_messages,
            "subtitle_count": len(self.subtitle_messages),
            "subtitle_enabled": self.subtitle_config.enabled,
            "config": self.config.model_dump(),
            "subtitle_config": self.subtitle_config.model_dump(),
        }
