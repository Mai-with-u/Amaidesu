"""
ReadPingmu InputProvider - 屏幕读评输入Provider

职责:
- 通过AI分析屏幕内容
- 将分析结果通过EventBus发送
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Literal, Optional

if TYPE_CHECKING:
    pass

from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.normalized_message import NormalizedMessage

# 导入屏幕分析和读取模块
try:
    from ..screen_analyzer import ScreenAnalyzer
    from ..screen_reader import ScreenReader

    SCREEN_MODULES_AVAILABLE = True
except ImportError:
    ScreenAnalyzer = None
    ScreenReader = None
    SCREEN_MODULES_AVAILABLE = False


class ReadPingmuInputProvider(InputProvider):
    """屏幕读评输入Provider"""

    class ConfigSchema(BaseProviderConfig):
        """屏幕读评输入Provider配置"""

        type: Literal["read_pingmu"] = "read_pingmu"
        api_key: str = Field(default="", description="API密钥")
        base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", description="API基础URL")
        model_name: str = Field(default="qwen2.5-vl-72b-instruct", description="模型名称")
        screenshot_interval: float = Field(default=0.3, description="截图间隔（秒）", ge=0.1)
        diff_threshold: float = Field(default=25.0, description="差异阈值", ge=0.0)
        check_window: int = Field(default=3, description="检查窗口", ge=1)
        max_cache_size: int = Field(default=5, description="最大缓存大小", ge=1)
        max_cached_images: int = Field(default=5, description="最大缓存图像数", ge=1)

    def __init__(self, config: Dict[str, Any]):
        """
        初始化ReadPingmu Provider

        Args:
            config: 配置字典，包含:
                - api_key: API密钥
                - base_url: API基础URL
                - model_name: 模型名称
                - screenshot_interval: 截图间隔
                - diff_threshold: 差异阈值
                - check_window: 检查窗口
                - max_cache_size: 最大缓存大小
                - max_cached_images: 最大缓存图像数
        """
        super().__init__(config)
        self.logger = get_logger("ReadPingmuInputProvider")
        self.typed_config = self.ConfigSchema(**config)

        # 组件
        self.screen_analyzer: Optional[ScreenAnalyzer] = None
        self.screen_reader: Optional[ScreenReader] = None

        # 运行状态
        self._running = False
        self._monitor_task = None

        # 统计信息
        self.messages_sent = 0
        self.last_message_time = 0.0

        # 消息队列（用于将回调转换为异步生成器）
        self._message_queue: Optional[asyncio.Queue] = None

    async def generate(self) -> AsyncIterator[NormalizedMessage]:
        """
        启动 Provider 并返回 NormalizedMessage 流

        Yields:
            NormalizedMessage: 屏幕描述消息
        """
        self.is_running = True

        # 创建消息队列
        self._message_queue = asyncio.Queue()

        # 检查依赖
        if not SCREEN_MODULES_AVAILABLE:
            self.logger.error("无法导入屏幕分析模块，请确保相关文件存在")
            return

        try:
            # 创建屏幕阅读器
            self.screen_reader = ScreenReader(
                api_key=self.typed_config.api_key,
                base_url=self.typed_config.base_url,
                model_name=self.typed_config.model_name,
                max_cached_images=self.typed_config.max_cached_images,
            )

            # 创建屏幕分析器
            self.screen_analyzer = ScreenAnalyzer(
                interval=self.typed_config.screenshot_interval,
                diff_threshold=self.typed_config.diff_threshold,
                check_window=self.typed_config.check_window,
                max_cache_size=self.typed_config.max_cache_size,
            )

            # 设置回调函数
            self.screen_reader.set_context_update_callback(self._on_context_update)
            self.screen_analyzer.set_change_callback(self._on_screen_change)

            # 启动屏幕监控
            await self.screen_analyzer.start()
            self._running = True

            # 启动后台任务
            self._monitor_task = asyncio.create_task(self._monitoring_loop(), name="ScreenMonitorLoop")

            self.logger.info(
                f"ReadPingmuInputProvider 已启动 (间隔: {self.typed_config.screenshot_interval}s, 阈值: {self.typed_config.diff_threshold})"
            )

            # 从队列中获取消息并yield
            while self.is_running:
                try:
                    normalized_msg = await asyncio.wait_for(self._message_queue.get(), timeout=1.0)
                    yield normalized_msg
                except asyncio.TimeoutError:
                    # 超时继续循环，检查is_running
                    continue

        except asyncio.CancelledError:
            self.logger.info("采集被取消")
        except Exception as e:
            self.logger.error(f"数据采集出错: {e}", exc_info=True)
        finally:
            self.is_running = False
            self.logger.info("ReadPingmuInputProvider 已停止")

    async def _monitoring_loop(self):
        """后台监控循环"""
        while self._running:
            await asyncio.sleep(1)

    async def _on_screen_change(self, change_data: Dict[str, Any]):
        """处理屏幕变化检测"""
        if not self._running or not self.screen_reader:
            return

        self.logger.debug(f"检测到屏幕变化: 差异分数 {change_data.get('difference_score', 0):.2f}")

        try:
            # 将变化数据传递给 screen_reader 进行分析
            result = await self.screen_reader.process_screen_change(change_data)
            if result:
                self.logger.debug(f"AI分析完成: {result.new_current_context[:50]}...")
            else:
                self.logger.debug("图像已缓存或分析失败")
        except Exception as e:
            self.logger.error(f"处理屏幕变化时出错: {e}", exc_info=True)

    async def _on_context_update(self, data: Dict[str, Any]):
        """处理上下文更新 - 创建 NormalizedMessage 并放入队列"""
        try:
            # 提取分析结果
            analysis_result = data.get("analysis_result")
            if not analysis_result:
                return

            new_context = analysis_result.new_current_context
            images_processed = data.get("images_processed", 1)

            if not new_context:
                return

            # 直接创建 NormalizedMessage
            normalized_msg = NormalizedMessage(
                text=new_context,
                source="read_pingmu",
                data_type="text",
                importance=0.5,
                raw={
                    "images_processed": images_processed,
                    "statistics": data.get("statistics", {}),
                    "user": "屏幕分析",
                    "user_id": "screen_analyzer",
                },
            )

            # 放入队列
            if self._message_queue:
                await self._message_queue.put(normalized_msg)

            self.messages_sent += 1
            self.last_message_time = time.time()

            self.logger.info(f"屏幕描述消息已创建: {new_context[:50]}...")

        except Exception as e:
            self.logger.error(f"创建屏幕描述消息失败: {e}", exc_info=True)

    async def _cleanup_internal(self):
        """清理资源"""
        if self._running and self.screen_analyzer:
            self.logger.info("正在停止屏幕监控...")
            await self.screen_analyzer.stop()
            self._running = False

        # 取消后台任务
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        self.logger.info("ReadPingmuInputProvider 已清理")
