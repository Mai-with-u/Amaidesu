# Screen Monitor Plugin - 屏幕监控插件（新架构）

from typing import Dict, Any, List

from src.utils.logger import get_logger


class ScreenMonitorPlugin:
    """屏幕监控插件（新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None
        self.logger = get_logger("ScreenMonitorPlugin")

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """初始化插件"""
        self.event_bus = event_bus
        self.config = config

        # 注册事件监听
        self.event_bus.on("screen_monitor.update", self._handle_update)

        self.logger.info("ScreenMonitorPlugin 初始化完成")
        return []

    async def cleanup(self):
        """清理资源"""
        if self.event_bus:
            self.event_bus.off("screen_monitor.update", self._handle_update)

        self.logger.info("ScreenMonitorPlugin 清理完成")

    async def _handle_update(self, event_name: str, data: Any, source: str):
        """处理屏幕更新事件"""
        self.logger.debug(f"收到屏幕更新: {data}")

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "ScreenMonitor",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "屏幕监控插件",
            "category": "input",
            "api_version": "2.0",
        }


plugin_entrypoint = ScreenMonitorPlugin
