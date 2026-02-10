# src/plugins/bili_danmaku_official/plugin.py

import asyncio
import contextlib
import json
import signal
from typing import Any, Dict, List, Optional

from src.core.amaidesu_core import AmaidesuCore

# --- Amaidesu Core Imports ---
from src.core.plugin_manager import BasePlugin

# --- Local Imports ---
from .client.websocket_client import BiliWebSocketClient
from .service.message_cache import MessageCacheService
from .service.message_handler import BiliMessageHandler


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
            import websockets  # 延迟导入
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


class BiliDanmakuOfficialMaiCraftPlugin(BasePlugin):
    """Bilibili 直播弹幕插件（官方WebSocket版），使用官方开放平台API获取实时弹幕。"""

    def __init__(self, core: AmaidesuCore, config: Dict[str, Any]):
        super().__init__(core, config)

        # --- 显式加载自己目录下的 config.toml ---
        self.config = self.plugin_config
        self.enabled = self.config.get("enabled", True)

        if not self.enabled:
            self.logger.warning("BiliDanmakuOfficialMaiCraftPlugin 在配置中已禁用。")
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

        # --- 模板信息已移除 ---
        self.template_items = None

        # --- 外发目标配置 ---
        self.forward_ws_url: Optional[str] = self.config.get("forward_ws_url")
        self.forward_enabled: bool = self.config.get("forward_enabled", True)

        # --- 状态变量 ---
        self.websocket_client = None
        self.message_handler = None
        self.monitoring_task = None
        self.stop_event = asyncio.Event()
        self.forward_client: Optional[ForwardWebSocketClient] = None

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
                message_cache_service=self.message_cache_service,
            )

            # 初始化外发 WebSocket 客户端
            if self.forward_enabled and self.forward_ws_url:
                self.forward_client = ForwardWebSocketClient(self.forward_ws_url, self.logger)
                await self.forward_client.start()
                self.logger.info(f"已启用外发 WebSocket 转发: {self.forward_ws_url}")
            else:
                if not self.forward_enabled:
                    self.logger.info("已禁用外发 WebSocket 转发")
                else:
                    self.logger.warning("未配置 forward_ws_url，外发 WebSocket 转发不可用")

            # 启动后台监控任务
            self.monitoring_task = asyncio.create_task(
                self._run_monitoring_loop(), name=f"BiliDanmakuOfficial_{self.app_id}"
            )
            self.logger.info(f"启动 Bilibili 官方WebSocket弹幕监控任务 (应用ID: {self.app_id})...")

        except Exception as e:
            self.logger.error(f"设置 BiliDanmakuOfficialMaiCraftPlugin 时发生错误: {e}", exc_info=True)
            self.enabled = False

    async def cleanup(self):
        """清理资源"""
        async with self.cleanup_lock:
            if self.is_shutting_down and hasattr(self, "_cleanup_done"):
                return  # 避免重复清理

            self.logger.info("开始清理 BiliDanmakuOfficialMaiCraft 插件资源...")
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

                # 关闭外发客户端
                if self.forward_client:
                    self.logger.info("关闭外发 WebSocket 客户端...")
                    try:
                        await self.forward_client.close()
                    except Exception as e:
                        self.logger.warning(f"关闭外发客户端时出错: {e}")
                    finally:
                        self.forward_client = None

                self.logger.info("BiliDanmakuOfficialMaiCraft 插件资源清理完成")
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
                # 外发到指定 WebSocket
                if self.forward_client and self.forward_enabled and self.forward_ws_url:
                    try:
                        payload = message.to_dict()
                        sent = await self.forward_client.send_json(payload)
                        if not sent:
                            self.logger.debug("外发失败，消息未送达")
                    except Exception as e:
                        self.logger.error(f"外发消息序列化或发送失败: {e}")
        except Exception as e:
            self.logger.error(f"处理消息时出错: {message_data} - {e}", exc_info=True)


# --- Plugin Entry Point ---
plugin_entrypoint = BiliDanmakuOfficialMaiCraftPlugin
