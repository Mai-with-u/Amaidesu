# src/plugins/bili_danmaku_official/plugin.py

import asyncio
import contextlib
import signal

from typing import Dict, Any, Optional, List

# --- Amaidesu Core Imports ---
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore

# --- Local Imports ---
from .client.websocket_client import BiliWebSocketClient
from .service.message_cache import MessageCacheService
from .service.message_handler import BiliMessageHandler


class BiliDanmakuOfficialPlugin(BasePlugin):
    """Bilibili 直播弹幕插件（官方WebSocket版），使用官方开放平台API获取实时弹幕。"""

    def __init__(self, core: AmaidesuCore, config: Dict[str, Any]):
        super().__init__(core, config)

        # --- 显式加载自己目录下的 config.toml ---
        self.config = self.plugin_config
        self.enabled = self.config.get("enabled", True)

        if not self.enabled:
            self.logger.warning("BiliDanmakuOfficialPlugin 在配置中已禁用。")
            return

        # --- 官方API配置检查 ---
        self.id_code = self.config.get("id_code")
        self.app_id = self.config.get("app_id")
        self.access_key = self.config.get("access_key")
        self.access_key_secret = self.config.get("access_key_secret")
        self.api_host = self.config.get("api_host", "https://live-open.biliapi.com")

        # 验证必需的配置项
        required_configs = ["id_code", "app_id", "access_key", "access_key_secret"]
        if missing_configs := [key for key in required_configs if not self.config.get(key)]:
            self.logger.error(f"缺少必需的配置项: {missing_configs}. 插件已禁用。")
            self.enabled = False
            return

        # --- Prompt Context Tags ---
        self.context_tags: Optional[List[str]] = self.config.get("context_tags")
        if not isinstance(self.context_tags, list):
            if self.context_tags is not None:
                self.logger.warning(f"配置 'context_tags' 不是列表类型 ({type(self.context_tags)}), 将获取所有上下文。")
            self.context_tags = None
        elif not self.context_tags:
            self.logger.info("'context_tags' 为空，将获取所有上下文。")
            self.context_tags = None
        else:
            self.logger.info(f"将获取具有以下标签的上下文: {self.context_tags}")

        # --- Load Template Items ---
        self.template_items = None
        if self.config.get("enable_template_info", False):
            self.template_items = self.config.get("template_items", {})
            if not self.template_items:
                self.logger.warning(
                    "BiliDanmakuOfficial 配置启用了 template_info，但在 config.toml 中未找到 template_items。"
                )

        # --- 状态变量 ---
        self.websocket_client = None
        self.message_handler = None
        self.monitoring_task = None
        self.stop_event = asyncio.Event()

        # --- 初始化消息缓存服务 ---
        cache_size = self.config.get("message_cache_size", 1000)
        self.message_cache_service = MessageCacheService(max_cache_size=cache_size)


        # --- 添加退出机制相关属性 ---
        self.shutdown_timeout = self.config.get("shutdown_timeout", 30)  # 30秒超时
        self.cleanup_lock = asyncio.Lock()
        self.is_shutting_down = False

        # --- 注册信号处理器 ---
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """设置信号处理器以实现优雅退出"""

        def signal_handler(signum, frame):
            self.logger.info(f"接收到信号 {signum}，开始优雅关闭...")
            asyncio.create_task(self._graceful_shutdown())

        # 注册常见的退出信号
        try:
            signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
            signal.signal(signal.SIGTERM, signal_handler)  # 终止信号
            if hasattr(signal, "SIGBREAK"):  # Windows
                signal.signal(signal.SIGBREAK, signal_handler)
        except Exception as e:
            self.logger.warning(f"设置信号处理器失败: {e}")

    async def _graceful_shutdown(self):
        """优雅关闭流程"""
        if self.is_shutting_down:
            return

        self.is_shutting_down = True
        self.logger.info("开始优雅关闭流程...")

        try:
            # 设置停止事件
            self.stop_event.set()

            # 等待监控任务完成
            if self.monitoring_task and not self.monitoring_task.done():
                self.logger.info("等待监控任务完成...")
                try:
                    await asyncio.wait_for(self.monitoring_task, timeout=self.shutdown_timeout)
                except asyncio.TimeoutError:
                    self.logger.warning(f"监控任务在 {self.shutdown_timeout} 秒内未完成，强制取消")
                    self.monitoring_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self.monitoring_task
            # 执行清理
            await self.cleanup()
            self.logger.info("优雅关闭完成")

        except Exception as e:
            self.logger.error(f"优雅关闭过程中发生错误: {e}")
        finally:
            self.is_shutting_down = False

    async def setup(self):
        await super().setup()
        if not self.enabled:
            return

        try:
            # 注册消息缓存服务到 core
            self.core.register_service("message_cache", self.message_cache_service)
            self.logger.info("消息缓存服务已注册到 AmaidesuCore")

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
                core=self.core,
                config=self.config,
                context_tags=self.context_tags,
                template_items=self.template_items,
                message_cache_service=self.message_cache_service,
            )

            # 启动后台监控任务
            self.monitoring_task = asyncio.create_task(
                self._run_monitoring_loop(), name=f"BiliDanmakuOfficial_{self.app_id}"
            )
            self.logger.info(f"启动 Bilibili 官方WebSocket弹幕监控任务 (应用ID: {self.app_id})...")

        except Exception as e:
            self.logger.error(f"设置 BiliDanmakuOfficialPlugin 时发生错误: {e}", exc_info=True)
            self.enabled = False

    async def cleanup(self):
        """清理资源"""
        async with self.cleanup_lock:
            if self.is_shutting_down and hasattr(self, "_cleanup_done"):
                return  # 避免重复清理

            self.logger.info("开始清理 BiliDanmakuOfficial 插件资源...")
            self.is_shutting_down = True

            try:
                # 设置停止事件
                self.stop_event.set()

                # 取消监控任务
                if self.monitoring_task and not self.monitoring_task.done():
                    self.logger.info("取消监控任务...")
                    self.monitoring_task.cancel()
                    try:
                        await asyncio.wait_for(self.monitoring_task, timeout=2.0)  # 减少到2秒
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        self.logger.info("监控任务已取消或超时")

                # 清理WebSocket客户端
                if self.websocket_client:
                    self.logger.info("关闭WebSocket客户端...")
                    try:
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

                self.logger.info("BiliDanmakuOfficial 插件资源清理完成")
                self._cleanup_done = True

            except Exception as e:
                self.logger.error(f"清理过程中发生错误: {e}")

    async def _run_monitoring_loop(self):
        """运行监控循环"""
        self.logger.info("开始监控 Bilibili 官方WebSocket连接...")
        consecutive_errors = 0
        max_consecutive_errors = 5

        try:
            while not self.stop_event.is_set():
                try:
                    # 检查是否正在关闭
                    if self.is_shutting_down:
                        self.logger.info("检测到关闭信号，退出监控循环")
                        break

                    # 启动WebSocket连接并监听消息
                    await self.websocket_client.run(self._handle_message_from_bili)
                    consecutive_errors = 0  # 重置错误计数

                except Exception as e:
                    consecutive_errors += 1
                    self.logger.error(f"监控循环中发生错误 ({consecutive_errors}/{max_consecutive_errors}): {e}")

                    # 如果连续错误过多，等待更长时间
                    if consecutive_errors >= max_consecutive_errors:
                        self.logger.warning("连续错误过多，等待60秒后重试...")
                        await asyncio.sleep(60)
                    else:
                        await asyncio.sleep(10)

                # 检查停止信号
                if self.stop_event.is_set():
                    break

        except asyncio.CancelledError:
            self.logger.info("监控循环被取消")
        except Exception as e:
            self.logger.error(f"监控循环发生未预期的错误: {e}", exc_info=True)
        finally:
            self.logger.info("监控循环已结束")

    async def _handle_message_from_bili(self, message_data: Dict[str, Any]):
        """处理从 Bilibili 接收到的消息"""
        try:
            message = await self.message_handler.create_message_base(message_data)
            if message:
                # 将消息缓存到消息缓存服务中
                self.message_cache_service.cache_message(message)
                self.logger.debug(f"消息已缓存: {message.message_info.message_id}")
                # 发送消息到 MaiCore
                await self.core.send_to_maicore(message)
        except Exception as e:
            self.logger.error(f"处理消息时出错: {message_data} - {e}", exc_info=True)


# --- Plugin Entry Point ---
plugin_entrypoint = BiliDanmakuOfficialPlugin
