"""
弹幕小部件服务。

订阅 ``room.message.danmaku``（语义域事件）并广播给前端 WebSocket
客户端；字幕显示由字幕基础设施 ``SubtitleService`` 通过
``DashboardBackend`` 驱动——本服务暴露 ``show_subtitle`` /
``clear_subtitle`` 公开方法供 Backend 调用，自身不再订阅
``planner.checkpoint`` 等业务事件做字幕拉取（语义错位的历史路径）。
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
from src.modules.events.payloads import RoomMessagePayload
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus


class DanmakuWidgetService:
    """
    弹幕小部件服务

    订阅输入阶段的消息事件，转换为 DanmakuWidgetMessage 并广播到前端。
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
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            self._on_input_message,
            model_class=RoomMessagePayload,
        )

        self._is_running = True
        self.logger.info(f"DanmakuWidgetService 已启动 (max_messages={self.config.max_messages})")

    async def stop(self) -> None:
        if not self._is_running:
            return

        self.event_bus.off(CoreEvents.ROOM_MESSAGE_DANMAKU, self._on_input_message)

        self._is_running = False
        self.logger.info("DanmakuWidgetService 已停止")

    async def _on_input_message(
        self,
        event_name: str,
        payload: RoomMessagePayload,
        source: str,
    ) -> None:
        try:
            text = payload.content
            self.logger.debug(f"收到弹幕: {text[:50]}, user={payload.user.name if payload.user else ''}")
            widget_msg = self._convert_payload_to_widget(payload)
            if widget_msg is None:
                # 退化：尝试从 payload.model_dump() 取字段
                dump = payload.model_dump() if hasattr(payload, "model_dump") else {}
                widget_msg = self._convert_dict_to_widget(dump)
            if widget_msg is None:
                return

            if not self._should_display(widget_msg):
                return

            self.messages.append(widget_msg)

            await self._broadcast_new_message(widget_msg)

        except Exception as e:
            self.logger.error(f"处理输入消息失败: {e}", exc_info=True)

    async def show_subtitle(self, text: str, duration_ms: Optional[int] = None) -> None:
        """显示一条字幕（字幕基础设施 Backend 的调用入口）。

        构造 ``SubtitleWidgetMessage`` 并追加到字幕队列，然后通过
        ``_subtitle_callback`` / ``_broadcast_callback`` 推送给前端
        WebSocket 客户端（``/ws/subtitle``）。由 ``DashboardBackend.show``
        在 ``SubtitleService.show`` 广播路径上调用。

        Args:
            text: 字幕文本内容。
            duration_ms: 自动隐藏时长（毫秒）；``None`` 时回退到
                ``subtitle_config.auto_hide_after_ms``。合法范围由
                ``SubtitleWidgetMessage`` Pydantic Schema 校验。
        """
        if not self.subtitle_config.enabled:
            return

        effective_duration_ms = duration_ms if duration_ms is not None else self.subtitle_config.auto_hide_after_ms
        subtitle_msg = SubtitleWidgetMessage(
            text=text,
            duration_ms=effective_duration_ms,
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

    async def clear_subtitle(self) -> None:
        """清空当前字幕显示（字幕基础设施 Backend 的调用入口）。

        清空本地字幕队列并广播一条空文本消息（前端按 ``text == ""``
        识别为清空信号，``duration_ms == 0`` 抑制自动隐藏计时）。由
        ``DashboardBackend.clear`` 在 ``SubtitleService.clear`` 广播
        路径上调用。
        """
        if not self.subtitle_config.enabled:
            self.subtitle_messages.clear()
            return

        self.subtitle_messages.clear()

        data = {
            "type": "subtitle",
            "text": "",
            "duration_ms": 0,
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
                self.logger.error(f"广播清空字幕到subtitle端失败: {e}", exc_info=True)

        if self._broadcast_callback:
            try:
                await self._broadcast_callback(data)
            except Exception as e:
                self.logger.error(f"广播清空字幕失败: {e}", exc_info=True)

    def _convert_payload_to_widget(
        self,
        payload: RoomMessagePayload,
    ) -> Optional[DanmakuWidgetMessage]:
        """RoomMessagePayload 直转（v2 主路径，结构化字段）。"""
        try:
            user_name = payload.user.name if payload.user else "匿名用户"
            user_id = payload.user.id if payload.user else ""
            timestamp = (
                datetime.fromtimestamp(payload.timestamp_ms / 1000.0) if payload.timestamp_ms else datetime.now()
            )
            importance = 0.5
            platform = "unknown"
            room_id = payload.live_session_id or None

            if payload.message_type == "gift":
                return DanmakuWidgetMessage(
                    user_name=user_name,
                    user_id=user_id,
                    content=payload.content or "",
                    message_type=MessageType.GIFT,
                    timestamp=timestamp,
                    importance=importance,
                    gift_name=payload.gift.name if payload.gift else None,
                    gift_count=payload.gift.count if payload.gift else None,
                    platform=platform,
                    room_id=room_id,
                )

            if payload.message_type == "super_chat":
                return DanmakuWidgetMessage(
                    user_name=user_name,
                    user_id=user_id,
                    content=payload.content or "",
                    message_type=MessageType.SUPER_CHAT,
                    timestamp=timestamp,
                    importance=importance,
                    sc_price=payload.sc.amount if payload.sc else None,
                    sc_message=payload.content or None,
                    platform=platform,
                    room_id=room_id,
                )

            if payload.message_type == "enter":
                return DanmakuWidgetMessage(
                    user_name=user_name,
                    user_id=user_id,
                    content=payload.content or "",
                    message_type=MessageType.ENTER,
                    timestamp=timestamp,
                    importance=importance,
                    platform=platform,
                    room_id=room_id,
                )

            return DanmakuWidgetMessage(
                user_name=user_name,
                user_id=user_id,
                content=payload.content or "",
                message_type=MessageType.TEXT,
                timestamp=timestamp,
                importance=importance,
                platform=platform,
                room_id=room_id,
            )

        except Exception as e:
            self.logger.error(f"转换 RoomMessagePayload 失败: {e}", exc_info=True)
            return None

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
                    message_type=MessageType.TEXT,
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
                    message_type=MessageType.TEXT,
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

        if msg.message_type == MessageType.TEXT and not self.config.show_danmaku:
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
