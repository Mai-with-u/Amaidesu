# src/plugins/bili_danmaku_official_maicraft/plugin.py

from typing import Dict, Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.event_bus import EventBus

from src.core.base.input_provider import InputProvider
from src.utils.logger import get_logger
from .providers.bili_official_maicraft_provider import BiliDanmakuOfficialMaiCraftInputProvider


class BiliDanmakuOfficialMaiCraftPlugin:
    """
    Bilibili 官方弹幕+Minecraft转发插件

    迁移到新的Plugin接口：
    - 不继承BasePlugin
    - 实现Plugin协议
    - 返回Provider列表（BiliDanmakuOfficialMaiCraftInputProvider）
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus: Optional["EventBus"] = None
        self._providers: List[InputProvider] = []

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("BiliDanmakuOfficialMaiCraftPlugin 在配置中已禁用。")
            return

        # 验证必需配置
        required_configs = ["id_code", "app_id", "access_key", "access_key_secret"]
        if missing_configs := [key for key in required_configs if not self.config.get(key)]:
            self.logger.error(f"缺少必需的配置项: {missing_configs}. 插件已禁用。")
            self.enabled = False
            return

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（包含BiliDanmakuOfficialMaiCraftInputProvider）
        """
        self.event_bus = event_bus

        if not self.enabled:
            self.logger.warning("BiliDanmakuOfficialMaiCraftPlugin 未启用，不创建Provider。")
            return []

        # 创建Provider
        try:
            provider = BiliDanmakuOfficialMaiCraftInputProvider(self.config)
            self._providers.append(provider)
            self.logger.info("BiliDanmakuOfficialMaiCraftInputProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建 BiliDanmakuOfficialMaiCraftInputProvider 失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 BiliDanmakuOfficialMaiCraftPlugin...")

        # 清理所有Provider
        for provider in self._providers:
            try:
                await provider.stop()
            except Exception as e:
                self.logger.error(f"清理 Provider 时出错: {e}", exc_info=True)

        self._providers.clear()
        self.logger.info("BiliDanmakuOfficialMaiCraftPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "BiliDanmakuOfficialMaiCraft",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "Bilibili 官方弹幕+Minecraft转发插件",
            "category": "input",
            "api_version": "1.0",
        }


# --- Plugin Entry Point ---
plugin_entrypoint = BiliDanmakuOfficialMaiCraftPlugin
