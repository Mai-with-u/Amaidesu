# OBS Control Plugin - OBS控制插件（新架构）

from typing import Dict, Any, List

from src.utils.logger import get_logger
from .providers.obs_control_output_provider import ObsControlOutputProvider


class ObsControlPlugin:
    """OBS控制插件（新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger("ObsControlPlugin")

        self.event_bus = None
        self._providers: List[Any] = []

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（包含ObsControlOutputProvider）
        """
        self.event_bus = event_bus

        # 创建 ObsControlOutputProvider
        try:
            provider = ObsControlOutputProvider(self.config)
            await provider.setup(event_bus)
            self._providers.append(provider)
            self.logger.info("ObsControlOutputProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建 ObsControlOutputProvider 失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 ObsControlPlugin...")

        # 清理所有 Provider
        for provider in self._providers:
            try:
                await provider.cleanup()
            except Exception as e:
                self.logger.error(f"清理 Provider 时出错: {e}", exc_info=True)

        self._providers.clear()

        self.logger.info("ObsControlPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "ObsControl",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "OBS控制插件",
            "category": "software",
            "api_version": "2.0",
        }


plugin_entrypoint = ObsControlPlugin
