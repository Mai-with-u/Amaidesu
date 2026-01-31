# Maicraft Plugin - Minecraft弹幕互动插件（新架构）

from typing import Dict, Any

from src.core.plugin import Plugin
from src.core.event_bus import EventBus
from src.utils.logger import get_logger


class MaicraftPlugin(Plugin):
    """Minecraft弹幕互动游戏插件（新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None
        self.logger = get_logger("MaicraftPlugin")

        self.enabled = config.get("enabled", True)
        self.factory_type = config.get("factory_type", "log")

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> list:
        """初始化插件"""
        self.event_bus = event_bus
        self.config = config

        if not self.enabled:
            self.logger.info("Maicraft插件已禁用")
            return []

        # 注册事件监听
        self.event_bus.on("command_router.received", self._handle_command)
        self.event_bus.on("maicraft.execute", self._handle_execute)

        self.logger.info(f"MaicraftPlugin 初始化完成 (工厂类型: {self.factory_type})")
        return []

    async def cleanup(self):
        """清理资源"""
        if self.event_bus:
            self.event_bus.off("command_router.received", self._handle_command)
            self.event_bus.off("maicraft.execute", self._handle_execute)

        self.logger.info("MaicraftPlugin 清理完成")

    async def _handle_command(self, event_name: str, data: Dict[str, Any], source: str):
        """处理命令事件"""
        command = data.get("command", "")
        if command:
            self.logger.info(f"收到命令: {command}")

    async def _handle_execute(self, event_name: str, data: Any, source: str):
        """处理执行事件"""
        self.logger.debug(f"收到执行请求: {data}")

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "Maicraft",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "Minecraft弹幕互动游戏插件",
            "category": "game",
            "api_version": "2.0",
        }


plugin_entrypoint = MaicraftPlugin
