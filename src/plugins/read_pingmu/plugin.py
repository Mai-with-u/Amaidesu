# Amaidesu Read Pingmu Plugin - 屏幕读评插件（新架构）
#
# 依赖:
# - pip install openai  (OpenAI 兼容 API 客户端)
# - pip install pillow  (图像处理，用于拼接功能)
# - pip install mss     (屏幕截图)
#
# 功能:
# - 自动启动屏幕分析和AI读取
# - 将AI分析结果通过EventBus发送
# - 提供上下文服务

import asyncio
import time
from typing import Dict, Any, Optional

from src.core.plugin import Plugin
from src.core.event_bus import EventBus
from src.utils.logger import get_logger

# 导入屏幕分析和读取模块
try:
    from .screen_analyzer import ScreenAnalyzer
    from .screen_reader import ScreenReader

    SCREEN_MODULES_AVAILABLE = True
except ImportError:
    ScreenAnalyzer = None
    ScreenReader = None
    SCREEN_MODULES_AVAILABLE = False


class ReadPingmuPlugin(Plugin):
    """
    屏幕读评插件（新架构）

    通过EventBus通信，发送屏幕分析事件。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None

        # Logger
        self.logger = get_logger("ReadPingmuPlugin")

        # 组件
        self.screen_analyzer: Optional[ScreenAnalyzer] = None
        self.screen_reader: Optional[ScreenReader] = None

        # 运行状态
        self._running = False
        self._monitor_task = None

        # 统计信息
        self.messages_sent = 0
        self.last_message_time = 0.0

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> list:
        """
        初始化插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（此插件不返回Provider，直接使用EventBus）
        """
        self.event_bus = event_bus
        self.config = config

        if not config.get("enabled", True):
            self.logger.info("屏幕监控插件已禁用")
            return []

        # 检查依赖
        if not SCREEN_MODULES_AVAILABLE:
            self.logger.error("无法导入屏幕分析模块，请确保相关文件存在")
            return []

        try:
            # API配置
            api_key = config.get("api_key", "")
            base_url = config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            model_name = config.get("model_name", "qwen2.5-vl-72b-instruct")

            # 分析器配置
            screenshot_interval = config.get("screenshot_interval", 0.3)
            diff_threshold = config.get("diff_threshold", 25.0)
            check_window = config.get("check_window", 3)
            max_cache_size = config.get("max_cache_size", 5)
            max_cached_images = config.get("max_cached_images", 5)

            # 消息配置
            message_config = config.get("message", {})

            # 创建屏幕阅读器
            self.screen_reader = ScreenReader(
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
                max_cached_images=max_cached_images,
            )

            # 创建屏幕分析器
            self.screen_analyzer = ScreenAnalyzer(
                interval=screenshot_interval,
                diff_threshold=diff_threshold,
                check_window=check_window,
                max_cache_size=max_cache_size,
            )

            # 设置回调函数
            self.screen_reader.set_context_update_callback(self._on_context_update)
            self.screen_analyzer.set_change_callback(self._on_screen_change)

            # 启动屏幕监控
            await self.screen_analyzer.start()
            self._running = True

            # 启动后台任务
            self._monitor_task = asyncio.create_task(self._monitoring_loop(), name="ScreenMonitorLoop")

            self.logger.info(f"屏幕监控插件已启动 (间隔: {screenshot_interval}s, 阈值: {diff_threshold})")

        except Exception as e:
            self.logger.error(f"启动屏幕监控插件失败: {e}", exc_info=True)

        return []

    async def cleanup(self):
        """插件清理"""
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

        self.logger.info("屏幕监控插件已停止")

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
        """处理上下文更新 - 发送消息到核心系统"""
        try:
            # 提取分析结果
            analysis_result = data.get("analysis_result")
            if not analysis_result:
                return

            new_context = analysis_result.new_current_context
            images_processed = data.get("images_processed", 1)

            # 通过EventBus发送事件
            if self.event_bus:
                await self.event_bus.emit(
                    "screen_monitor.update",
                    {
                        "description": new_context,
                        "images_processed": images_processed,
                        "statistics": data.get("statistics", {}),
                        "timestamp": time.time(),
                    },
                    source="read_pingmu",
                )

            self.messages_sent += 1
            self.last_message_time = time.time()

            self.logger.info(f"屏幕描述消息已发送: {new_context[:50]}...")

        except Exception as e:
            self.logger.error(f"发送屏幕描述消息失败: {e}", exc_info=True)

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "ReadPingmu",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "屏幕读评插件 - 通过AI分析屏幕内容",
            "category": "input",
            "api_version": "2.0",
        }


# 插件入口点
plugin_entrypoint = ReadPingmuPlugin
