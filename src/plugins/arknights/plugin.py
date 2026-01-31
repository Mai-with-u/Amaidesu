# Arknights Plugin - 新Plugin架构实现

from typing import Dict, Any, List

from src.utils.logger import get_logger


class ArknightsPlugin:
    """
    明日方舟插件 - 让麦麦游玩明日方舟（空壳）

    迁移到新的Plugin架构，保持为空壳状态。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus = None

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("ArknightsPlugin 在配置中已禁用。")
            return

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（空列表，这是空壳插件）
        """
        self.event_bus = event_bus

        if not self.enabled:
            return []

        self.logger.info("ArknightsPlugin 设置完成（空壳，待实现）")

        return []

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 ArknightsPlugin...")
        self.logger.info("ArknightsPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "Arknights",
            "version": "0.1.0",
            "author": "Amaidesu Team",
            "description": "明日方舟插件，让麦麦游玩明日方舟（空壳，待实现）",
            "category": "game",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = ArknightsPlugin
