"""
Sticker Plugin

贴纸插件，处理表情图片并发送到VTS显示。
迁移到新的Plugin架构。
"""

from typing import Dict, Any, List

from src.core.event_bus import EventBus
from src.plugins.sticker.sticker_output_provider import StickerOutputProvider
from src.utils.logger import get_logger


class StickerPlugin:
    """
    贴纸插件

    使用OutputProvider处理表情图片并发送到VTS显示。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        # Provider列表
        self._providers: List[StickerOutputProvider] = []

        self.event_bus: EventBus = None

        if not self.config.get("enabled", True):
            self.logger.warning("StickerPlugin 在配置中被禁用。")
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
        self.logger.info("设置StickerPlugin")

        if not self.enabled:
            return []

        # 使用sticker配置节点
        sticker_config = config.get("sticker", {})
        if not sticker_config:
            sticker_config = config  # 向后兼容

        # 创建OutputProvider
        output_provider = StickerOutputProvider(sticker_config, event_bus)
        await output_provider.setup(event_bus, sticker_config)
        self._providers.append(output_provider)

        self.logger.info(f"StickerPlugin 设置完成，已创建 {len(self._providers)} 个Provider。")

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("清理StickerPlugin...")

        # 清理所有Provider
        for provider in self._providers:
            await provider.cleanup()
        self._providers.clear()

        self.logger.info("StickerPlugin清理完成。")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "Sticker",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "贴纸插件，处理表情图片并发送到VTS显示",
            "category": "output",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = StickerPlugin
