# src/plugins/bili_danmaku/plugin.py

from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    pass

# --- Core Imports ---
from src.core.base.input_provider import InputProvider
from src.utils.logger import get_logger
from .providers.bili_danmaku_provider import BiliDanmakuInputProvider


class BiliDanmakuPlugin:
    """
    Bilibili 直播弹幕插件，连接到直播间并接收弹幕/礼物等事件。

    迁移到新的Plugin接口：
    - 不继承BasePlugin
    - 实现Plugin协议
    - 返回Provider列表（BiliDanmakuInputProvider）
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus = None
        self._providers: List[InputProvider] = []

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("BiliDanmakuPlugin 在配置中已禁用。")
            return

        # 验证必需配置
        room_id = self.config.get("room_id")
        if not room_id or not isinstance(room_id, int) or room_id <= 0:
            self.logger.error(f"Invalid or missing 'room_id' in config: {room_id}. Plugin disabled.")
            self.enabled = False
            return

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（包含BiliDanmakuInputProvider）
        """
        self.event_bus = event_bus

        if not self.enabled:
            self.logger.warning("BiliDanmakuPlugin 未启用，不创建Provider。")
            return []

        # 创建 BiliDanmakuInputProvider
        try:
            provider = BiliDanmakuInputProvider(self.config)
            self._providers.append(provider)
            self.logger.info("BiliDanmakuInputProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建 BiliDanmakuInputProvider 失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 BiliDanmakuPlugin...")

        # 清理所有 Provider
        for provider in self._providers:
            try:
                await provider.stop()
            except Exception as e:
                self.logger.error(f"清理 Provider 时出错: {e}", exc_info=True)

        self._providers.clear()
        self.logger.info("BiliDanmakuPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "BiliDanmaku",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "Bilibili 直播弹幕插件，通过轮询 API 获取实时弹幕",
            "category": "input",
            "api_version": "1.0",
        }


# --- Plugin Entry Point ---
plugin_entrypoint = BiliDanmakuPlugin
