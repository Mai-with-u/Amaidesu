"""
BiliDanmakuOfficialCollector —— Bilibili 官方弹幕采集器

- 继承 ``BaseCollector``（流型感知者，世界→系统入口，主动推事件）
- 默认 emit ``room.message.*`` 语义域事件（danmaku/gift/super_chat/enter）
- 仍保留 ``collect()`` AsyncIterator 出口，供旧 InputCollectorManager 过渡期复用
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, Optional

from pydantic import Field

from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import (
    GiftInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types.bili import (
    BiliBaseMessage,
    BiliMessageType,
    BiliMessageTypeConfig,
    DanmakuMessage,
    EnterMessage,
    GiftMessage,
    GuardMessage,
    SuperChatMessage,
)

from .client.websocket_client import BiliWebSocketClient


class BiliDanmakuOfficialCollector(BaseCollector):
    """Bilibili 官方弹幕采集器

    使用官方 WebSocket API 实时接收弹幕/SC/礼物/进房事件，emit
    ``room.message.*`` 语义域事件（默认）；同时仍 yield 事件载荷
    以兼容旧 InputCollectorManager 过渡期。
    """

    name = "bili_danmaku_official"
    description = "通过 Bilibili 官方开放平台 WebSocket API 采集弹幕/SC/礼物/进房事件"

    class ConfigSchema(BaseConfig):
        """Bilibili官方弹幕采集器配置"""

        id_code: str = Field(..., description="主播身份码")
        app_id: str = Field(..., description="应用ID")
        access_key: str = Field(..., description="访问密钥")
        access_key_secret: str = Field(..., description="访问密钥Secret")
        api_host: str = Field(default="https://live-open.biliapi.com", description="API主机地址")
        message_cache_size: int = Field(default=1000, description="消息缓存大小", ge=1)
        context_tags: Optional[list] = Field(default=None, description="Prompt上下文标签")
        enable_template_info: bool = Field(default=False, description="启用模板信息")
        template_items: dict = Field(default_factory=dict, description="模板项")
        # 是否 emit 语义域事件（默认 True）
        emit_semantic_events: bool = Field(default=True, description="emit room.message.* 语义事件")

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ):
        """初始化 BiliDanmakuOfficialCollector

        Args:
            config: 配置字典
            event_bus: 事件总线实例（可选）
        """
        super().__init__(event_bus=event_bus)
        self.config = config or {}
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(self.config)
        self.id_code = self.typed_config.id_code
        self.app_id = self.typed_config.app_id
        self.access_key = self.typed_config.access_key
        self.access_key_secret = self.typed_config.access_key_secret
        self.api_host = self.typed_config.api_host
        self._emit_semantic_events = self.typed_config.emit_semantic_events

        self.message_type_config = BiliMessageTypeConfig(self.config)

        self.websocket_client: Optional[BiliWebSocketClient] = None
        self.is_started = False  # 兼容旧 InputCollectorManager 的 is_started 检查

        # context_tags 处理
        self.context_tags: Optional[list] = self.typed_config.context_tags
        if not isinstance(self.context_tags, list):
            if self.context_tags is not None:
                self.logger.warning(f"配置 'context_tags' 不是列表类型 ({type(self.context_tags)}), 将获取所有上下文。")
            self.context_tags = None
        elif not self.context_tags:
            self.logger.info("'context_tags' 为空，将获取所有上下文。")
            self.context_tags = None
        else:
            self.logger.info(f"将获取具有以下标签的上下文: {self.context_tags}")

        self.template_items = None
        if self.typed_config.enable_template_info:
            self.template_items = self.typed_config.template_items
            if not self.template_items:
                self.logger.warning(
                    "BiliDanmakuOfficial 配置启用了 template_info，但在 config/input.toml 中未找到 template_items。"
                )

    # ------------------------------------------------------------------
    # 旧 InputCollectorManager 兼容接口
    # ------------------------------------------------------------------

    def stream(self) -> AsyncIterator[RoomMessagePayload]:
        """返回 room.message.* 事件载荷流（旧 InputCollectorManager 过渡期使用）"""
        if not self.is_started:
            raise RuntimeError("Collector 未启动，请先调用 start()")

        async def _generate():
            try:
                async for message in self.collect():
                    yield message
            finally:
                self.is_started = False

        return _generate()

    async def start(self) -> None:
        """启动：开后台任务消费 collect()（内部 emit room.message.*）。"""
        if not self.is_started:
            self.is_started = True
            await self._start_collect_task()
            self.logger.debug("BiliDanmakuOfficialCollector 后台采集任务已启动")

    async def stop(self) -> None:
        """停止：取消后台采集任务。"""
        if self.is_started:
            self.is_started = False
            await self._stop_collect_task()
            self.logger.debug("BiliDanmakuOfficialCollector.is_started 已清空")

    async def cleanup(self) -> None:
        """清理资源"""
        if self.websocket_client:
            try:
                self.logger.info("关闭WebSocket客户端...")
                await self.websocket_client.close()
                self.logger.info("WebSocket客户端已成功关闭")
            except Exception as e:
                self.logger.error(f"关闭WebSocket客户端时发生异常: {e}")
            finally:
                self.websocket_client = None

        self.logger.info("BiliDanmakuOfficialCollector 已清理")

    async def collect(self) -> AsyncIterator[RoomMessagePayload]:
        """采集弹幕数据（流式）；同时 emit room.message.* 语义域事件"""
        self.is_started = True

        message_queue: asyncio.Queue = asyncio.Queue()

        self.websocket_client = BiliWebSocketClient(
            id_code=self.id_code,
            app_id=self.app_id,
            access_key=self.access_key,
            access_key_secret=self.access_key_secret,
            api_host=self.api_host,
        )

        self.logger.info("开始采集 Bilibili 官方弹幕数据...")

        ws_task = asyncio.create_task(self._run_websocket(message_queue))

        try:
            while self.is_started:
                try:
                    payload = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                    if payload is None:
                        self.logger.info("收到结束信号，停止数据采集")
                        break
                    yield payload
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.logger.error(f"从队列获取消息时出错: {e}", exc_info=True)
                    break

        except asyncio.CancelledError:
            self.logger.info("采集被取消")
        except Exception as e:
            self.logger.error(f"数据采集出错: {e}", exc_info=True)
        finally:
            self.is_started = False
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass
            self.logger.info("Bilibili 官方弹幕采集已停止")

    async def _run_websocket(self, message_queue: asyncio.Queue) -> None:
        """运行WebSocket连接并将消息放入队列"""
        try:
            await self.websocket_client.run(self._handle_message_from_bili, message_queue)
        except Exception as e:
            self.logger.error(f"WebSocket运行出错: {e}", exc_info=True)
        finally:
            await message_queue.put(None)

    async def _handle_message_from_bili(self, message_data: Dict[str, Any], message_queue: asyncio.Queue) -> None:
        """处理从 Bilibili 接收到的消息"""
        try:
            cmd = message_data.get("cmd", "")

            if not self.message_type_config.should_handle(cmd):
                return

            bili_message = self._create_message_from_dict(message_data)
            if not bili_message:
                self.logger.debug(f"无法解析消息类型: {cmd}")
                return

            payload = self._create_payload(bili_message)

            # emit 语义域事件 room.message.*（受配置门控）
            if payload is not None and self._emit_semantic_events:
                await self._emit_semantic_event(payload)

            if payload is not None:
                self.logger.debug(f"消息已处理: {payload.content[:50]}...")
                await message_queue.put(payload)

        except Exception as e:
            self.logger.error(f"处理消息时出错: {e}", exc_info=True)
            self.logger.debug(f"失败消息数据: cmd={message_data.get('cmd')}")

    async def _emit_semantic_event(self, payload: RoomMessagePayload) -> None:
        """按载荷的 message_type 选事件名并 emit room.message.* 事件。"""
        event_map = {
            "danmaku": CoreEvents.ROOM_MESSAGE_DANMAKU,
            "enter": CoreEvents.ROOM_MESSAGE_ENTER,
            "gift": CoreEvents.ROOM_MESSAGE_GIFT,
            "super_chat": CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
        }
        event_name = event_map.get(payload.message_type)
        if event_name is None:
            self.logger.debug(f"未知 message_type '{payload.message_type}'，跳过 emit")
            return
        await self.emit_event(event_name, payload)

    def _create_message_from_dict(self, data: Dict[str, Any]) -> Optional[BiliBaseMessage]:
        """从字典创建对应的消息对象"""
        cmd = data.get("cmd", "")

        if cmd == BiliMessageType.DANMAKU.value:
            return DanmakuMessage.from_dict(data)
        elif cmd == BiliMessageType.ENTER.value:
            return EnterMessage.from_dict(data)
        elif cmd == BiliMessageType.GIFT.value:
            return GiftMessage.from_dict(data)
        elif cmd == BiliMessageType.GUARD.value:
            return GuardMessage.from_dict(data)
        elif cmd == BiliMessageType.SUPER_CHAT.value:
            return SuperChatMessage.from_dict(data)
        else:
            return None

    def _create_payload(self, bili_msg: BiliBaseMessage) -> Optional[RoomMessagePayload]:
        """从 B 站消息构造 room.message.* 事件载荷。

        场次归属（live_session_id）由事件总线的场次盖章拦截器统一注入，
        采集器不感知"当前是哪一场"。Guard 无独立 room.message.* 事件，
        返回 None（与既有行为一致：不进事件流）。
        """
        user_id = str(getattr(bili_msg, "open_id", None) or "unknown")
        user_name = str(getattr(bili_msg, "uname", None) or "unknown")
        timestamp_ms = int(bili_msg.timestamp * 1000) if bili_msg.timestamp else now_ms()

        if isinstance(bili_msg, DanmakuMessage):
            self.logger.debug(f"[弹幕] {bili_msg.uname}: {bili_msg.msg}")
            return RoomMessagePayload(
                message_type="danmaku",
                user=RoomMessageUser(id=user_id, name=user_name),
                content=bili_msg.msg,
                timestamp_ms=timestamp_ms,
            )

        if isinstance(bili_msg, EnterMessage):
            self.logger.debug(f"[进入] {bili_msg.uname} 进入了直播间")
            return RoomMessagePayload(
                message_type="enter",
                user=RoomMessageUser(id=user_id, name=user_name),
                content="",
                timestamp_ms=timestamp_ms,
            )

        if isinstance(bili_msg, GiftMessage):
            actual_num = max(bili_msg.gift_num, bili_msg.combo_info.combo_count)
            gift_name = bili_msg.gift_name or "礼物"
            self.logger.debug(f"[礼物] {bili_msg.uname} 送出了 {actual_num} 个 {gift_name}")
            return RoomMessagePayload(
                message_type="gift",
                user=RoomMessageUser(id=user_id, name=user_name),
                content="",
                gift=GiftInfo(name=gift_name, count=actual_num),
                timestamp_ms=timestamp_ms,
            )

        if isinstance(bili_msg, SuperChatMessage):
            self.logger.debug(f"[SC] {bili_msg.uname}: {bili_msg.message}")
            return RoomMessagePayload(
                message_type="super_chat",
                user=RoomMessageUser(id=user_id, name=user_name),
                content=bili_msg.message,
                sc=SuperChatInfo(amount=float(bili_msg.rmb)),
                timestamp_ms=timestamp_ms,
            )

        # Guard 等其余类型：无独立 room.message.* 事件
        self.logger.debug(f"消息类型无对应 room.message.* 事件，跳过: {type(bili_msg).__name__}")
        return None
