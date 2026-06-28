"""
ReadPingmuCollector - 屏幕读评输入Collector

通过AI分析屏幕内容，将分析结果通过EventBus发送。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, Literal, Optional

from pydantic import Field

from src.stages.input.registry import collector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.types.base.normalized_message import NormalizedMessage

try:
    from ..screen_analyzer import ScreenAnalyzer
    from ..screen_reader import ScreenReader

    SCREEN_MODULES_AVAILABLE = True
except ImportError:
    ScreenAnalyzer = None
    ScreenReader = None
    SCREEN_MODULES_AVAILABLE = False

# 后台监控循环心跳间隔（秒）
_MONITOR_INTERVAL_S = 1.0


@collector("read_pingmu")
class ReadPingmuCollector:
    """
    屏幕读评输入Collector

    通过AI分析屏幕内容，将分析结果通过EventBus发送。
    """

    class ConfigSchema(BaseConfig):
        """屏幕读评输入Collector配置"""

        type: Literal["read_pingmu"] = "read_pingmu"
        api_key: str = Field(default="", description="API密钥")
        base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", description="API基础URL")
        model_name: str = Field(default="qwen2.5-vl-72b-instruct", description="模型名称")
        screenshot_interval: float = Field(default=0.3, description="截图间隔（秒）", ge=0.1)
        diff_threshold: float = Field(default=25.0, description="差异阈值", ge=0.0)
        check_window: int = Field(default=3, description="检查窗口", ge=1)
        max_cache_size: int = Field(default=5, description="最大缓存大小", ge=1)
        max_cached_images: int = Field(default=5, description="最大缓存图像数", ge=1)

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
    ):
        """
        初始化ReadPingmuCollector

        Args:
            config: 配置字典
            event_bus: 事件总线实例
        """
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)
        self.typed_config = self.ConfigSchema.from_dict(config)

        self.screen_analyzer: Optional[ScreenAnalyzer] = None
        self.screen_reader: Optional[ScreenReader] = None
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self.messages_sent = 0
        self.last_message_time = 0.0
        self._message_queue: Optional[asyncio.Queue] = None
        self.is_started = False

    def stream(self) -> AsyncIterator[NormalizedMessage]:
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
        self.is_started = True

    async def stop(self) -> None:
        self.is_started = False

    async def cleanup(self) -> None:
        if self._running and self.screen_analyzer:
            self.logger.info("正在停止屏幕监控...")
            await self.screen_analyzer.stop()
            self._running = False

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        self.logger.info("ReadPingmuCollector 已清理")

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """启动并返回 NormalizedMessage 流"""
        self.is_started = True
        self._message_queue = asyncio.Queue()

        if not SCREEN_MODULES_AVAILABLE:
            self.logger.error("无法导入屏幕分析模块，请确保相关文件存在")
            return

        try:
            self.screen_reader = ScreenReader(
                api_key=self.typed_config.api_key,
                base_url=self.typed_config.base_url,
                model_name=self.typed_config.model_name,
                max_cached_images=self.typed_config.max_cached_images,
            )

            self.screen_analyzer = ScreenAnalyzer(
                interval=self.typed_config.screenshot_interval,
                diff_threshold=self.typed_config.diff_threshold,
                check_window=self.typed_config.check_window,
                max_cache_size=self.typed_config.max_cache_size,
            )

            self.screen_reader.set_context_update_callback(self._on_context_update)
            self.screen_analyzer.set_change_callback(self._on_screen_change)

            await self.screen_analyzer.start()
            self._running = True

            self._monitor_task = asyncio.create_task(self._monitoring_loop(), name="ScreenMonitorLoop")

            self.logger.info(
                f"ReadPingmuCollector 已启动 (间隔: {self.typed_config.screenshot_interval}s, 阈值: {self.typed_config.diff_threshold})"
            )

            while self.is_started:
                try:
                    normalized_msg = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
                    yield normalized_msg
                except asyncio.TimeoutError:
                    continue

        except asyncio.CancelledError:
            self.logger.info("采集被取消")
        except Exception as e:
            self.logger.error(f"数据采集出错: {e}", exc_info=True)
        finally:
            self.is_started = False
            self.logger.info("ReadPingmuCollector 已停止")

    async def _monitoring_loop(self) -> None:
        """后台监控循环"""
        while self._running:
            await asyncio.sleep(_MONITOR_INTERVAL_S)

    async def _on_screen_change(self, change_data: Dict[str, Any]) -> None:
        """处理屏幕变化检测"""
        if not self._running or not self.screen_reader:
            return

        self.logger.debug(f"检测到屏幕变化: 差异分数 {change_data.get('difference_score', 0):.2f}")

        try:
            result = await self.screen_reader.process_screen_change(change_data)
            if result:
                self.logger.debug(f"AI分析完成: {result.new_current_context[:50]}...")
            else:
                self.logger.debug("图像已缓存或分析失败")
        except Exception as e:
            self.logger.error(f"处理屏幕变化时出错: {e}", exc_info=True)

    async def _on_context_update(self, data: Dict[str, Any]) -> None:
        """处理上下文更新 - 创建 NormalizedMessage 并放入队列"""
        try:
            analysis_result = data.get("analysis_result")
            if not analysis_result:
                return

            new_context = analysis_result.new_current_context

            if not new_context:
                return

            normalized_msg = NormalizedMessage(
                text=new_context,
                source="read_pingmu",
                data_type="text",
                importance=0.5,
                user_id="screen_analyzer",
                user_nickname="屏幕分析",
                platform="screen",
            )

            if self._message_queue:
                await self._message_queue.put(normalized_msg)

            self.messages_sent += 1
            self.last_message_time = time.time()

            self.logger.debug(f"屏幕描述消息已创建: {new_context[:50]}...")

        except Exception as e:
            self.logger.error(f"创建屏幕描述消息失败: {e}", exc_info=True)
