# VRChat Plugin - VRChat控制（新架构）

from typing import Dict, Any, List

from src.utils.logger import get_logger


class VRChatPlugin:
    """VRChat虚拟形象控制插件（新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None
        self.logger = get_logger("VRChatPlugin")

        # OSC配置
        self.vrc_host = config.get("vrc_host", "127.0.0.1")
        self.vrc_port = config.get("vrc_port", 9000)
        self.enabled = config.get("enabled", True)

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """初始化插件"""
        self.event_bus = event_bus
        self.config = config

        if not self.enabled:
            self.logger.info("VRChat插件已禁用")
            return []

        # 注册事件监听
        self.event_bus.on("vrc.send_parameter", self._handle_parameter)
        self.event_bus.on("vrc.send_expression", self._handle_expression)

        self.logger.info("VRChatPlugin 初始化完成")
        return []

    async def cleanup(self):
        """清理资源"""
        if self.event_bus:
            self.event_bus.off("vrc.send_parameter", self._handle_parameter)
            self.event_bus.off("vrc.send_expression", self._handle_expression)

        self.logger.info("VRChatPlugin 清理完成")

    async def _handle_parameter(self, event_name: str, data: Any, source: str):
        """处理参数设置事件"""
        self.logger.debug(f"收到参数设置事件: {data}")

    async def _handle_expression(self, event_name: str, data: Any, source: str):
        """处理表情事件"""
        self.logger.debug(f"收到表情事件: {data}")

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "VRChat",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "VRChat虚拟形象控制插件",
            "category": "output",
            "api_version": "2.0",
        }


plugin_entrypoint = VRChatPlugin
