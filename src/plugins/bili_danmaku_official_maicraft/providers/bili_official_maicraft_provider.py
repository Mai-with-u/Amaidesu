"""
Bilibili 官方弹幕+Minecraft转发 InputProvider

从 Bilibili 官方开放平台 WebSocket API 采集弹幕数据并转发到Minecraft服务器。
"""

import asyncio
import contextlib
from typing import AsyncIterator, Dict, Any, Optional

from .client.websocket_client import BiliWebSocketClient
from .service.message_cache import MessageCacheService
from .service.message_handler import BiliMessageHandler

from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from src.utils.logger import get_logger


class ForwardWebSocketClient:
    """简易的外发 WebSocket 客户端，支持自动重连与发送JSON。"""

    def __init__(self, url: str, logger, reconnect_delay: int = 5):
        self.url = url
        self.logger = logger
        self.reconnect_delay = reconnect_delay
        self._ws = None
        self._task = None
        self._stop = asyncio.Event()

    async def run(self):
        try:
            import websockets
        except Exception as e:
            self.logger.error(f"缺少 websockets 依赖，无法转发消息: {e}")
            return

        while not self._stop.is_set():
            try:
                self.logger.info(f"尝试连接外发 WebSocket: {self.url}")
                async with websockets.connect(self.url) as ws:
                    self._ws = ws
                    self.logger.info("外发 WebSocket 已连接")
                    # 等待直到停止事件或连接断开
                    while not self._stop.is_set():
                        await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.warning(f"外发 WebSocket 连接失败/已断开: {e}")
            finally:
                self._ws = None
                if not self._stop.is_set():
                    self.logger.info(f"{self.reconnect_delay} 秒后重试连接外发 WebSocket...")
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.reconnect_delay)
                    except asyncio.TimeoutError:
                        pass

    async def send_json(self, data: Dict[str, Any]) -> bool:
        if self._ws is None:
            self.logger.debug("外发 WebSocket 未连接，丢弃消息")
            return False
        try:
            import json

            await self._ws.send(json.dumps(data, ensure_ascii=False))
            return True
        except Exception as e:
            self.logger.error(f"外发 WebSocket 发送失败: {e}")
            return False

    async def start(self):
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self.run(), name="ForwardWebSocketClient")

    async def close(self):
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None


class BiliDanmakuOfficialMaiCraftInputProvider(InputProvider):
    """
    Bilibili 官方弹幕+Minecraft转发 InputProvider

    使用官方WebSocket API获取实时弹幕，并转发到Minecraft服务器。
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

        # Template Items (已移除)
        self.template_items = None

        # 外发目标配置
        self.forward_ws_url: Optional[str] = config.get("forward_ws_url")
        self.forward_enabled: bool = config.get("forward_enabled", True)

        # 状态变量
        self.websocket_client = None
        self.message_handler = None
        self.message_cache_service = None
        self.forward_client: Optional[ForwardWebSocketClient] = None
        self._stop_event = asyncio.Event()

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

        # 初始化外发WebSocket客户端
        if self.forward_enabled and self.forward_ws_url:
            self.forward_client = ForwardWebSocketClient(self.forward_ws_url, self.logger)
            await self.forward_client.start()
            self.logger.info(f"已启用外发 WebSocket 转发: {self.forward_ws_url}")
        else:
            if not self.forward_enabled:
                self.logger.info("已禁用外发 WebSocket 转发")
            else:
                self.logger.warning("未配置 forward_ws_url，外发 WebSocket 转发不可用")

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

                # 外发到指定 WebSocket
                if self.forward_client and self.forward_enabled and self.forward_ws_url:
                    try:
                        payload = message.to_dict()
                        sent = await self.forward_client.send_json(payload)
                        if not sent:
                            self.logger.debug("外发失败，消息未送达")
                    except Exception as e:
                        self.logger.error(f"外发消息序列化或发送失败: {e}")

                # 创建RawData
                raw_data = RawData(
                    content={
                        "message": message,
                        "message_config": self.message_handler.get_message_config(),
                        "forward_url": self.forward_ws_url if self.forward_enabled else None,
                    },
                    source="bili_danmaku_official_maicraft",
                    data_type="text",
                    timestamp=message.message_info.time,
                    metadata={
                        "message_id": message.message_info.message_id,
                        "room_id": self.config.get("id_code"),
                        "forward_enabled": self.forward_enabled,
                    },
                )
                yield raw_data

        except Exception as e:
            self.logger.error(f"处理消息时出错: {message_data} - {e}", exc_info=True)

    async def _cleanup(self):
        """清理资源"""
        # 关闭WebSocket客户端
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

        # 关闭外发客户端
        if self.forward_client:
            self.logger.info("关闭外发 WebSocket 客户端...")
            try:
                await self.forward_client.close()
            except Exception as e:
                self.logger.warning(f"关闭外发客户端时出错: {e}")
            finally:
                self.forward_client = None

        self.logger.info("BiliDanmakuOfficialMaiCraftInputProvider 已清理")
