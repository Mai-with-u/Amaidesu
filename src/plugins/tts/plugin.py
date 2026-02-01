# src/plugins/tts/plugin.py

from typing import Dict, Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.event_bus import EventBus

from src.core.base.output_provider import OutputProvider
from src.utils.logger import get_logger
from src.layers.rendering.providers.tts_provider import TTSProvider as TTSOutputProvider


class TTSPlugin:
    """
    TTS语音合成插件

    迁移到新的Plugin接口：
    - 不继承BasePlugin
    - 实现Plugin协议
    - 返回Provider列表（TTSOutputProvider）
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus: Optional["EventBus"] = None
        self._providers: List[OutputProvider] = []

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("TTSPlugin 在配置中已禁用。")
            return

        # 依赖检查
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            self.logger.error("edge_tts library not found. Please install it (`pip install edge-tts`).")
            self.enabled = False
            return

        try:
            import sounddevice  # noqa: F401
        except ImportError:
            self.logger.error("sounddevice library not found. Please install it.")
            self.enabled = False
            return

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（包含TTSOutputProvider）
        """
        self.event_bus = event_bus

        if not self.enabled:
            self.logger.warning("TTSPlugin 未启用，不创建Provider。")
            return []

        # 创建Provider
        try:
            provider = TTSOutputProvider(self.config)
            await provider.setup(event_bus)
            self._providers.append(provider)
            self.logger.info("TTSOutputProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建 TTSOutputProvider 失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 TTSPlugin...")

        # 清理所有Provider
        for provider in self._providers:
            try:
                await provider.cleanup()
            except Exception as e:
                self.logger.error(f"清理 Provider 时出错: {e}", exc_info=True)

        self._providers.clear()
        self.logger.info("TTSPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "TTS",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "Edge TTS语音合成插件",
            "category": "output",
            "api_version": "1.0",
        }


# --- Plugin Entry Point ---
plugin_entrypoint = TTSPlugin
