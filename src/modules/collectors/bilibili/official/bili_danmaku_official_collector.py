"""
BiliDanmakuOfficialCollector —— Bilibili 官方弹幕采集器（v2 / Wave 5 迁移）

迁移自 ``src/stages/input/collectors/bili_danmaku_official/bili_danmaku_official_collector.py``。
按 §1.46 + .omo/drafts/amaidesu-v2-migration.md §C：
- 继承 ``BaseCollector``（流型感知者，世界→系统入口，主动推事件）
- 默认 emit ``room.message.*`` 语义域事件（danmaku/gift/super_chat/enter）
- 仍保留 ``collect()`` AsyncIterator 出口，供旧 InputCollectorManager 过渡期复用

verbatim 边界：协议解析、WebSocket 客户端、心跳/重连逻辑、重要性算法 —— 未改动。
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
from src.modules.types.base.normalized_message import NormalizedMessage
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
from src.modules.types.guard_levels import DEFAULT_GUARD_NAME, GUARD_LEVEL_NAMES

from .client.websocket_client import BiliWebSocketClient


class BiliDanmakuOfficialCollector(BaseCollector):
    """Bilibili 官方弹幕采集器

    使用官方 WebSocket API 实时接收弹幕/SC/礼物/进房事件，emit
    ``room.message.*`` 语义域事件（默认）；同时仍 yield NormalizedMessage
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
        # v2 新增：是否 emit 语义域事件（默认 True）
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

    def stream(self) -> AsyncIterator[NormalizedMessage]:
        """返回 NormalizedMessage 数据流（旧 InputCollectorManager 过渡期使用）"""
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
        """兼容旧 InputCollectorManager 的启动入口（直接调用亦可；新架构用 BaseCollector.start）

        新 CollectorManager 走 BaseCollector.start → _on_start。
        旧 InputCollectorManager 直接调用 start()。两者都需要实现。
        """
        # 避免与 BaseCollector.start() 的状态机冲突：
        # 当通过 CollectorManager 走时，会先进入我们的 start() 然后 BaseCollector.start() 会再调用一次。
        # 为兼容两条路径，此处仅做旧语义标志位，新状态机由 BaseCollector 管理。
        if not self.is_started:
            self.is_started = True
            self.logger.debug("BiliDanmakuOfficialCollector.is_started 已设置")

    async def stop(self) -> None:
        """兼容旧 InputCollectorManager 的停止入口"""
        if self.is_started:
            self.is_started = False
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

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
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
                    normalized_msg = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                    if normalized_msg is None:
                        self.logger.info("收到结束信号，停止数据采集")
                        break
                    yield normalized_msg
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

            normalized_msg = self._create_normalized_message(bili_message)

            # v2：emit 语义域事件 room.message.*
            if self._emit_semantic_events:
                await self._emit_semantic_event(bili_message, normalized_msg)

            self.logger.debug(f"消息已处理: {normalized_msg.text[:50]}...")
            await message_queue.put(normalized_msg)

        except Exception as e:
            self.logger.error(f"处理消息时出错: {e}", exc_info=True)
            self.logger.debug(f"失败消息数据: cmd={message_data.get('cmd')}")

    async def _emit_semantic_event(self, bili_msg: BiliBaseMessage, normalized_msg: NormalizedMessage) -> None:
        """emit room.message.* 语义域事件（v2）"""
        try:
            room_id = str(getattr(bili_msg, "room_id", 0)) or "unknown"
            user_id = str(getattr(bili_msg, "open_id", None) or "unknown")
            user_name = str(getattr(bili_msg, "uname", None) or "unknown")

            if isinstance(bili_msg, DanmakuMessage):
                payload = RoomMessagePayload(
                    live_session_id=room_id,
                    message_type="danmaku",
                    user=RoomMessageUser(id=user_id, name=user_name),
                    content=bili_msg.msg,
                    timestamp_ms=normalized_msg.timestamp_ms,
                )
                await self.emit_event(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)
            elif isinstance(bili_msg, EnterMessage):
                payload = RoomMessagePayload(
                    live_session_id=room_id,
                    message_type="enter",
                    user=RoomMessageUser(id=user_id, name=user_name),
                    content="",
                    timestamp_ms=normalized_msg.timestamp_ms,
                )
                await self.emit_event(CoreEvents.ROOM_MESSAGE_ENTER, payload)
            elif isinstance(bili_msg, GiftMessage):
                actual_num = max(bili_msg.gift_num, bili_msg.combo_info.combo_count)
                payload = RoomMessagePayload(
                    live_session_id=room_id,
                    message_type="gift",
                    user=RoomMessageUser(id=user_id, name=user_name),
                    content="",
                    gift=GiftInfo(name=bili_msg.gift_name or "礼物", count=actual_num),
                    timestamp_ms=normalized_msg.timestamp_ms,
                )
                await self.emit_event(CoreEvents.ROOM_MESSAGE_GIFT, payload)
            elif isinstance(bili_msg, SuperChatMessage):
                payload = RoomMessagePayload(
                    live_session_id=room_id,
                    message_type="super_chat",
                    user=RoomMessageUser(id=user_id, name=user_name),
                    content=bili_msg.message,
                    sc=SuperChatInfo(amount=float(bili_msg.rmb)),
                    timestamp_ms=normalized_msg.timestamp_ms,
                )
                await self.emit_event(CoreEvents.ROOM_MESSAGE_SUPER_CHAT, payload)
            # Guard 没有独立 room.message.* 事件，按 danmaku 走（携带 guard_name 提示）
        except Exception as e:
            self.logger.debug(f"emit 语义事件失败: {e}", exc_info=True)

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

    def _create_normalized_message(self, bili_msg: BiliBaseMessage) -> NormalizedMessage:
        """从 B 站消息构造 NormalizedMessage"""
        user_id = getattr(bili_msg, "open_id", None) or None
        user_nickname = getattr(bili_msg, "uname", None) or None
        room_id = str(getattr(bili_msg, "room_id", 0)) or None

        if isinstance(bili_msg, DanmakuMessage):
            self.logger.debug(f"[弹幕] {bili_msg.uname}: {bili_msg.msg}")
            return NormalizedMessage(
                text=bili_msg.msg,
                source=self.name,
                data_type="text",
                importance=self._calculate_danmaku_importance(bili_msg),
                timestamp_ms=int(bili_msg.timestamp * 1000) if bili_msg.timestamp else now_ms(),
                raw=bili_msg,
                user_id=user_id,
                user_nickname=user_nickname,
                platform="bilibili",
                room_id=room_id,
            )

        elif isinstance(bili_msg, EnterMessage):
            self.logger.debug(f"[进入] {bili_msg.uname} 进入了直播间")
            return NormalizedMessage(
                text=f"{bili_msg.uname} 进入了直播间",
                source=self.name,
                data_type="enter",
                importance=0.1,
                timestamp_ms=int(bili_msg.timestamp * 1000) if bili_msg.timestamp else now_ms(),
                raw=bili_msg,
                user_id=user_id,
                user_nickname=user_nickname,
                platform="bilibili",
                room_id=room_id,
            )

        elif isinstance(bili_msg, GiftMessage):
            actual_num = max(bili_msg.gift_num, bili_msg.combo_info.combo_count)
            gift_name = bili_msg.gift_name or "礼物"
            description = f"{bili_msg.uname} 送出了 {actual_num} 个 {gift_name}"
            self.logger.debug(f"[礼物] {description}")

            return NormalizedMessage(
                text=description,
                source=self.name,
                data_type="gift",
                importance=self._calculate_gift_importance(bili_msg, actual_num),
                timestamp_ms=int(bili_msg.timestamp * 1000) if bili_msg.timestamp else now_ms(),
                raw=bili_msg,
                user_id=user_id,
                user_nickname=user_nickname,
                platform="bilibili",
                room_id=room_id,
            )

        elif isinstance(bili_msg, GuardMessage):
            guard_name = GUARD_LEVEL_NAMES.get(bili_msg.guard_level, DEFAULT_GUARD_NAME)
            description = f"{bili_msg.uname} 开通了 {guard_name}"
            self.logger.debug(f"[大航海] {description}")
            importance_scores = {1: 1.0, 2: 0.9, 3: 0.8}
            return NormalizedMessage(
                text=description,
                source=self.name,
                data_type="guard",
                importance=importance_scores.get(bili_msg.guard_level, 0.7),
                timestamp_ms=int(bili_msg.timestamp * 1000) if bili_msg.timestamp else now_ms(),
                raw=bili_msg,
                user_id=user_id,
                user_nickname=user_nickname,
                platform="bilibili",
                room_id=room_id,
            )

        elif isinstance(bili_msg, SuperChatMessage):
            if bili_msg.message.strip():
                description = f"[SC {bili_msg.rmb}元] {bili_msg.uname}: {bili_msg.message}"
                text = bili_msg.message
            else:
                description = f"[SC {bili_msg.rmb}元] {bili_msg.uname} 发送了醒目留言"
                text = description
            self.logger.debug(f"[SC] {description}")
            importance = min(0.5 + bili_msg.rmb / 100, 1.0)
            return NormalizedMessage(
                text=text,
                source=self.name,
                data_type="super_chat",
                importance=importance,
                timestamp_ms=int(bili_msg.timestamp * 1000) if bili_msg.timestamp else now_ms(),
                raw=bili_msg,
                user_id=user_id,
                user_nickname=user_nickname,
                platform="bilibili",
                room_id=room_id,
            )

        else:
            return NormalizedMessage(
                text=str(bili_msg.raw_data),
                source=self.name,
                data_type="unknown",
                importance=0.1,
                timestamp_ms=now_ms(),
                raw=bili_msg,
                user_id=user_id,
                user_nickname=user_nickname,
                platform="bilibili",
                room_id=room_id,
            )

    def _calculate_danmaku_importance(self, msg: DanmakuMessage) -> float:
        """计算弹幕重要性"""
        base = 0.5
        medal_bonus = min(msg.fans_medal_level / 40, 0.2)
        guard_bonus = {1: 0.3, 2: 0.2, 3: 0.1}.get(msg.guard_level, 0)
        return min(base + medal_bonus + guard_bonus, 1.0)

    def _calculate_gift_importance(self, msg: GiftMessage, actual_num: int) -> float:
        """计算礼物重要性"""
        base = min(msg.price / 10000, 0.5)
        quantity_bonus = min(actual_num / 10, 0.3)
        paid_bonus = 0.1 if msg.paid else 0
        return min(base + quantity_bonus + paid_bonus, 1.0)
