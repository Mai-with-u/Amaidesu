"""
Subtitle Plugin

字幕插件，使用CustomTkinter显示字幕窗口。
迁移到新的Plugin架构。
"""

import threading
from typing import Dict, Any, List

from src.core.event_bus import EventBus
from src.plugins.subtitle.subtitle_output_provider import SubtitleOutputProvider, CTK_AVAILABLE
from src.utils.logger import get_logger


class SubtitlePlugin:
    """
    字幕插件

    使用OutputProvider显示字幕窗口，支持描边和半透明背景。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        # Provider列表
        self._providers: List[SubtitleOutputProvider] = []

        self.event_bus: EventBus = None

        # 检查依赖
        if not CTK_AVAILABLE:
            self.logger.error("CustomTkinter库不可用,字幕插件已禁用。")
            self.enabled = False
            return

        if not self.config.get("enabled", True):
            self.logger.warning("SubtitlePlugin 在配置中被禁用。")
            self.enabled = False
            return

        self.enabled = True

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表
        """
        self.event_bus = event_bus
        self.logger.info("设置SubtitlePlugin")

        if not self.enabled:
            return []

        # 使用subtitle_display配置节点
        subtitle_config = config.get("subtitle_display", {})
        if not subtitle_config:
            subtitle_config = config  # 向后兼容

        # 创建OutputProvider
        output_provider = SubtitleOutputProvider(subtitle_config)
        await output_provider.setup(event_bus)
        self._providers.append(output_provider)

        # 启动GUI线程
        output_provider.is_running = True
        output_provider.gui_thread = threading.Thread(target=output_provider._run_gui, daemon=True)
        output_provider.gui_thread.start()
        self.logger.info("Subtitle GUI 线程已启动。")

        self.logger.info(f"SubtitlePlugin 设置完成，已创建 {len(self._providers)} 个Provider。")

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("清理SubtitlePlugin...")

        # 清理所有Provider
        for provider in self._providers:
            await provider.cleanup()
        self._providers.clear()

        self.logger.info("SubtitlePlugin清理完成。")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "Subtitle",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "字幕显示插件，使用CustomTkinter显示字幕窗口",
            "category": "output",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = SubtitlePlugin
