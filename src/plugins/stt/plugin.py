# Amaidesu STT Plugin - 语音识别插件（新架构）

from typing import Dict, Any

from src.core.plugin import Plugin
from src.core.event_bus import EventBus
from src.utils.logger import get_logger


class STTPlugin(Plugin):
    """语音识别插件（新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None
        self.logger = get_logger("STTPlugin")

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> list:
        """初始化插件"""
        self.event_bus = event_bus
        self.config = config

        # 注册事件监听
        self.event_bus.on("stt.start_recognition", self._handle_start)
        self.event_bus.on("stt.stop_recognition", self._handle_stop)

        self.logger.info("STTPlugin 初始化完成")
        return []

    async def cleanup(self):
        """清理资源"""
        if self.event_bus:
            self.event_bus.off("stt.start_recognition", self._handle_start)
            self.event_bus.off("stt.stop_recognition", self._handle_stop)

        self.logger.info("STTPlugin 清理完成")

    async def _handle_start(self, event_name: str, data: Any, source: str):
        """处理开始识别事件"""
        self.logger.debug(f"收到开始识别请求: {data}")

    async def _handle_stop(self, event_name: str, data: Any, source: str):
        """处理停止识别事件"""
        self.logger.debug(f"收到停止识别请求: {data}")

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "STT",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "语音识别插件",
            "category": "input",
            "api_version": "2.0",
        }


plugin_entrypoint = STTPlugin
