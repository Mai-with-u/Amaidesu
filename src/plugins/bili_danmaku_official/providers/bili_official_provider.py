"""
Bilibili 官方弹幕 InputProvider

从 Bilibili 官方开放平台 WebSocket API 采集弹幕数据。
"""

import asyncio
from typing import AsyncIterator, Dict, Any, Optional

from ..client.websocket_client import BiliWebSocketClient
from ..service.message_cache import MessageCacheService
from ..service.message_handler import BiliMessageHandler

from src.core.base.input_provider import InputProvider
from src.data_types.raw_data import RawData
from src.utils.logger import get_logger


class BiliDanmakuOfficialInputProvider(InputProvider):
    """
    Bilibili 官方弹幕 InputProvider

    使用官方WebSocket API获取实时弹幕。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

        # 配置
        self.id_code = config.get("id_code")
        self.app_id = config.get("app_id")
        self.access_key = config.get("access_key")
        self.access_key_secret = config.get("access_key_secret")
        self.api_host = config.get("api_host", "https://live-open.biliapi.com")

        # 验证必需配置
        required_configs = ["id_code", "app_id", "access_key", "access_key_secret"]
        if missing_configs := [key for key in required_configs if not config.get(key)]:
            raise ValueError(f"缺少必需的配置项: {missing_configs}")

        # 状态变量
        self.websocket_client = None
        self.message_handler = None
        self.message_cache_service = None
        self._stop_event = asyncio.Event()

        # Prompt Context Tags
        self.context_tags: Optional[list] = config.get("context_tags")
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
        if config.get("enable_template_info", False):
            self.template_items = config.get("template_items", {})
            if not self.template_items:
                self.logger.warning(
                    "BiliDanmakuOfficial 配置启用了 template_info，但在 config.toml 中未找到 template_items。"
                )

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集弹幕数据"""
        # 初始化消息缓存服务
        cache_size = self.config.get("message_cache_size", 1000)
        self.message_cache_service = MessageCacheService(max_cache_size=cache_size)

        # 初始化WebSocket客户端
        self.websocket_client = BiliWebSocketClient(
            id_code=self.id_code,
            app_id=self.app_id,
            access_key=self.access_key,
            access_key_secret=self.access_key_secret,
            api_host=self.api_host,
        )

        # 初始化消息处理器
        self.message_handler = BiliMessageHandler(
            config=self.config,
            context_tags=self.context_tags,
            template_items=self.template_items,
            message_cache_service=self.message_cache_service,
        )

        self.logger.info("开始采集 Bilibili 官方弹幕数据...")

        # 运行WebSocket连接
        try:
            await self.websocket_client.run(self._handle_message_from_bili)
        except Exception as e:
            self.logger.error(f"WebSocket运行出错: {e}", exc_info=True)

        self.logger.info("Bilibili 官方弹幕采集已停止")

    async def _handle_message_from_bili(self, message_data: Dict[str, Any]):
        """处理从Bilibili接收到的消息"""
        try:
            message = await self.message_handler.create_message_base(message_data)
            if message:
                # 缓存消息
                self.message_cache_service.cache_message(message)
                self.logger.debug(f"消息已缓存: {message.message_info.message_id}")

                # 创建RawData
                raw_data = RawData(
                    content={
                        "message": message,
                        "message_config": self.message_handler.get_message_config(),
                    },
                    source="bili_danmaku_official",
                    data_type="text",
                    timestamp=message.message_info.time,
                    metadata={
                        "message_id": message.message_info.message_id,
                        "room_id": self.config.get("id_code"),
                    },
                )
                yield raw_data

        except Exception as e:
            self.logger.error(f"处理消息时出错: {message_data} - {e}", exc_info=True)

    async def _cleanup(self):
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

        # 清理缓存服务
        if self.message_cache_service:
            try:
                self.message_cache_service.clear_cache()
                self.logger.info("消息缓存已清理")
            except Exception as e:
                self.logger.warning(f"清理消息缓存时出错: {e}")

        self.logger.info("BiliDanmakuOfficialInputProvider 已清理")
