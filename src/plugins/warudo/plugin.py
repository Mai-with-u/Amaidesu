# Warudo Plugin - Warudo虚拟形象控制（新架构）

from typing import Dict, Any

from src.core.plugin import Plugin
from src.core.event_bus import EventBus
from src.utils.logger import get_logger


class WarudoPlugin(Plugin):
    """Warudo虚拟形象控制插件（新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None
        self.logger = get_logger("WarudoPlugin")

        # WebSocket配置
        self.ws_host = config.get("ws_host", "localhost")
        self.ws_port = config.get("ws_port", 19190)
        self.websocket = None
        self._is_connected = False

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> list:
        """初始化插件"""
        self.event_bus = event_bus
        self.config = config

        # 注册事件监听
        self.event_bus.on("vts.send_emotion", self._handle_emotion)
        self.event_bus.on("vts.send_state", self._handle_state)
        self.event_bus.on("tts.audio_ready", self._handle_tts_audio)

        # 启动WebSocket连接
        await self._connect_websocket()

        self.logger.info("WarudoPlugin 初始化完成")
        return []

    async def cleanup(self):
        """清理资源"""
        if self.websocket:
            await self.websocket.close()

        if self.event_bus:
            self.event_bus.off("vts.send_emotion", self._handle_emotion)
            self.event_bus.off("vts.send_state", self._handle_state)
            self.event_bus.off("tts.audio_ready", self._handle_tts_audio)

        self.logger.info("WarudoPlugin 清理完成")

    async def _connect_websocket(self):
        """连接WebSocket服务器"""
        try:
            import websockets

            uri = f"ws://{self.ws_host}:{self.ws_port}"
            self.websocket = await websockets.connect(uri)
            self._is_connected = True
            self.logger.info(f"已连接到Warudo: {uri}")
        except Exception as e:
            self.logger.error(f"连接Warudo失败: {e}")

    async def _handle_emotion(self, event_name: str, data: Any, source: str):
        """处理表情事件"""
        self.logger.debug(f"收到表情事件: {data}")

    async def _handle_state(self, event_name: str, data: Any, source: str):
        """处理状态事件"""
        self.logger.debug(f"收到状态事件: {data}")

    async def _handle_tts_audio(self, event_name: str, data: Any, source: str):
        """处理TTS音频事件"""
        self.logger.debug("收到TTS音频事件")

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "Warudo",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "Warudo虚拟形象控制插件",
            "category": "output",
            "api_version": "2.0",
        }


plugin_entrypoint = WarudoPlugin
