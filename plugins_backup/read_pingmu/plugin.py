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

from typing import Dict, Any, List

from src.utils.logger import get_logger
from .providers.read_pingmu_input_provider import ReadPingmuInputProvider


class ReadPingmuPlugin:
    """
    屏幕读评插件（新架构）

    返回ReadPingmuInputProvider，通过EventBus通信，发送屏幕分析事件。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger("ReadPingmuPlugin")

        self.event_bus = None
        self._providers: List[Any] = []

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        初始化插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（包含ReadPingmuInputProvider）
        """
        self.event_bus = event_bus

        if not config.get("enabled", True):
            self.logger.info("屏幕监控插件已禁用")
            return []

        # 创建 ReadPingmuInputProvider
        try:
            provider = ReadPingmuInputProvider(self.config)
            await provider.setup(event_bus)
            self._providers.append(provider)
            self.logger.info("ReadPingmuInputProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建 ReadPingmuInputProvider 失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """插件清理"""
        self.logger.info("开始清理 ReadPingmuPlugin...")

        # 清理所有 Provider
        for provider in self._providers:
            try:
                await provider.cleanup()
            except Exception as e:
                self.logger.error(f"清理 Provider 时出错: {e}", exc_info=True)

        self._providers.clear()

        self.logger.info("ReadPingmuPlugin 清理完成")

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
