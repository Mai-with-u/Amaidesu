"""
Bilibili 官方弹幕 InputProvider

从 Bilibili 官方开放平台 WebSocket API 采集弹幕数据。
"""

import asyncio
import time
from typing import Any, AsyncIterator, Dict, Literal, Optional

from pydantic import Field

from src.domains.input.shared.bili_messages import (
    BiliBaseMessage,
    BiliMessageType,
    BiliMessageTypeConfig,
    DanmakuMessage,
    EnterMessage,
    GiftMessage,
    GuardMessage,
    SuperChatMessage,
)
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.normalized_message import NormalizedMessage

from .client.websocket_client import BiliWebSocketClient


class BiliDanmakuOfficialInputProvider(InputProvider):
    """
    Bilibili 官方弹幕 InputProvider

    使用官方WebSocket API获取实时弹幕。
    """

    class ConfigSchema(BaseProviderConfig):
        """Bilibili官方弹幕输入Provider配置"""

        type: Literal["bili_danmaku_official"] = "bili_danmaku_official"
        id_code: str = Field(..., description="直播间ID代码")
        app_id: str = Field(..., description="应用ID")
        access_key: str = Field(..., description="访问密钥")
        access_key_secret: str = Field(..., description="访问密钥Secret")
        api_host: str = Field(default="https://live-open.biliapi.com", description="API主机地址")
        message_cache_size: int = Field(default=1000, description="消息缓存大小", ge=1)
        context_tags: Optional[list] = Field(default=None, description="Prompt上下文标签")
        enable_template_info: bool = Field(default=False, description="启用模板信息")
        template_items: dict = Field(default_factory=dict, description="模板项")

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

        # 配置
        self.typed_config = self.ConfigSchema(**config)
        self.id_code = self.typed_config.id_code
        self.app_id = self.typed_config.app_id
        self.access_key = self.typed_config.access_key
        self.access_key_secret = self.typed_config.access_key_secret
        self.api_host = self.typed_config.api_host

        # 消息类型配置
        self.message_type_config = BiliMessageTypeConfig(config)

        # 状态变量
        self.websocket_client = None
        self._stop_event = asyncio.Event()

        # Prompt Context Tags
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

        # Template Items
        self.template_items = None
        if self.typed_config.enable_template_info:
            self.template_items = self.typed_config.template_items
            if not self.template_items:
                self.logger.warning(
                    "BiliDanmakuOfficial 配置启用了 template_info，但在 config.toml 中未找到 template_items。"
                )

    async def generate(self) -> AsyncIterator[NormalizedMessage]:
        """采集弹幕数据"""
        self.is_running = True

        # 创建队列用于接收消息
        message_queue = asyncio.Queue()

        # 初始化WebSocket客户端
        self.websocket_client = BiliWebSocketClient(
            id_code=self.id_code,
            app_id=self.app_id,
            access_key=self.access_key,
            access_key_secret=self.access_key_secret,
            api_host=self.api_host,
        )

        self.logger.info("开始采集 Bilibili 官方弹幕数据...")

        # 创建WebSocket任务
        ws_task = asyncio.create_task(self._run_websocket(message_queue))

        try:
            # 从队列中获取消息并yield
            while self.is_running:
                try:
                    # 设置超时以避免永久阻塞
                    normalized_msg = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                    if normalized_msg is None:
                        self.logger.info("收到结束信号，停止数据采集")
                        break
                    yield normalized_msg
                except asyncio.TimeoutError:
                    # 超时继续循环，检查is_running
                    continue
                except Exception as e:
                    self.logger.error(f"从队列获取消息时出错: {e}", exc_info=True)
                    break

        except asyncio.CancelledError:
            self.logger.info("采集被取消")
        except Exception as e:
            self.logger.error(f"数据采集出错: {e}", exc_info=True)
        finally:
            self.is_running = False
            # 停止WebSocket任务
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass
            self.logger.info("Bilibili 官方弹幕采集已停止")

    async def _run_websocket(self, message_queue: asyncio.Queue):
        """运行WebSocket连接并将消息放入队列"""
        try:
            await self.websocket_client.run(self._handle_message_from_bili, message_queue)
        except Exception as e:
            self.logger.error(f"WebSocket运行出错: {e}", exc_info=True)
        finally:
            # 通知队列结束
            await message_queue.put(None)

    async def _handle_message_from_bili(self, message_data: Dict[str, Any], message_queue: asyncio.Queue):
        """处理从 Bilibili 接收到的消息"""
        try:
            cmd = message_data.get("cmd", "")

            # 使用消息类型配置进行过滤
            if not self.message_type_config.should_handle(cmd):
                return

            # 从字典创建消息对象
            bili_message = self._create_message_from_dict(message_data)
            if not bili_message:
                self.logger.debug(f"无法解析消息类型: {cmd}")
                return

            # 直接构造 NormalizedMessage
            normalized_msg = self._create_normalized_message(bili_message)

            self.logger.debug(f"消息已处理: {normalized_msg.text[:50]}...")
            await message_queue.put(normalized_msg)

        except Exception as e:
            self.logger.error(f"处理消息时出错: {e}", exc_info=True)
            self.logger.debug(f"失败消息数据: cmd={message_data.get('cmd')}")

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
        if isinstance(bili_msg, DanmakuMessage):
            self.logger.info(f"[弹幕] {bili_msg.uname}: {bili_msg.msg}")
            return NormalizedMessage(
                text=bili_msg.msg,
                source="bili_danmaku_official",
                data_type="text",
                importance=self._calculate_danmaku_importance(bili_msg),
                timestamp=bili_msg.timestamp or time.time(),
                raw=bili_msg,
            )

        elif isinstance(bili_msg, EnterMessage):
            self.logger.info(f"[进入] {bili_msg.uname} 进入了直播间")
            return NormalizedMessage(
                text=f"{bili_msg.uname} 进入了直播间",
                source="bili_danmaku_official",
                data_type="enter",
                importance=0.1,
                timestamp=bili_msg.timestamp or time.time(),
                raw=bili_msg,
            )

        elif isinstance(bili_msg, GiftMessage):
            gift_name = bili_msg.gift_name or "礼物"
            description = f"{bili_msg.uname} 送出了 {bili_msg.gift_num} 个 {gift_name}"
            self.logger.info(f"[礼物] {description}")
            return NormalizedMessage(
                text=description,
                source="bili_danmaku_official",
                data_type="gift",
                importance=self._calculate_gift_importance(bili_msg),
                timestamp=bili_msg.timestamp or time.time(),
                raw=bili_msg,
            )

        elif isinstance(bili_msg, GuardMessage):
            guard_level_map = {1: "总督", 2: "提督", 3: "舰长"}
            guard_name = guard_level_map.get(bili_msg.guard_level, "大航海")
            description = f"{bili_msg.uname} 开通了 {guard_name}"
            self.logger.info(f"[大航海] {description}")
            importance_scores = {1: 1.0, 2: 0.9, 3: 0.8}
            return NormalizedMessage(
                text=description,
                source="bili_danmaku_official",
                data_type="guard",
                importance=importance_scores.get(bili_msg.guard_level, 0.7),
                timestamp=bili_msg.timestamp or time.time(),
                raw=bili_msg,
            )

        elif isinstance(bili_msg, SuperChatMessage):
            if bili_msg.message.strip():
                description = f"[SC {bili_msg.rmb}元] {bili_msg.uname}: {bili_msg.message}"
                text = bili_msg.message
            else:
                description = f"[SC {bili_msg.rmb}元] {bili_msg.uname} 发送了醒目留言"
                text = description
            self.logger.info(f"[SC] {description}")
            importance = min(0.5 + bili_msg.rmb / 100, 1.0)
            return NormalizedMessage(
                text=text,
                source="bili_danmaku_official",
                data_type="super_chat",
                importance=importance,
                timestamp=bili_msg.timestamp or time.time(),
                raw=bili_msg,
            )

        else:
            return NormalizedMessage(
                text=str(bili_msg.raw_data),
                source="bili_danmaku_official",
                data_type="unknown",
                importance=0.1,
                timestamp=time.time(),
                raw=bili_msg,
            )

    def _calculate_danmaku_importance(self, msg: DanmakuMessage) -> float:
        """计算弹幕重要性"""
        base = 0.5
        medal_bonus = min(msg.fans_medal_level / 40, 0.2)
        guard_bonus = {1: 0.3, 2: 0.2, 3: 0.1}.get(msg.guard_level, 0)
        return min(base + medal_bonus + guard_bonus, 1.0)

    def _calculate_gift_importance(self, msg: GiftMessage) -> float:
        """计算礼物重要性"""
        base = min(msg.price / 10000, 0.5)
        quantity_bonus = min(msg.gift_num / 10, 0.3)
        paid_bonus = 0.1 if msg.paid else 0
        return min(base + quantity_bonus + paid_bonus, 1.0)

    async def _cleanup_internal(self):
        """清理资源"""
        # 清理WebSocket客户端
        if self.websocket_client:
            try:
                self.logger.info("关闭WebSocket客户端...")
                await self.websocket_client.close()
                self.logger.info("WebSocket客户端已成功关闭")
            except Exception as e:
                self.logger.error(f"关闭WebSocket客户端时发生异常: {e}")
            finally:
                self.websocket_client = None

        self.logger.info("BiliDanmakuOfficialInputProvider 已清理")
