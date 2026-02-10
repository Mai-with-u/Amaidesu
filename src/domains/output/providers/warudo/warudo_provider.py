"""
Warudo OutputProvider - Warudo虚拟形象控制Provider

职责:
- 通过WebSocket连接到Warudo
- 接收表情、状态等事件并发送到Warudo
"""

from typing import Dict, Any, TYPE_CHECKING
from pydantic import Field

if TYPE_CHECKING:
    from src.core.event_bus import EventBus

from src.core.base.output_provider import OutputProvider
from src.core.events.names import CoreEvents
from src.core.utils.logger import get_logger
from src.services.config.schemas.schemas.base import BaseProviderConfig


class WarudoOutputProvider(OutputProvider):
    """Warudo虚拟形象控制Provider"""

    class ConfigSchema(BaseProviderConfig):
        """Warudo输出Provider配置"""

        type: str = "warudo"

        # WebSocket配置
        ws_host: str = Field(default="localhost", description="WebSocket主机地址")
        ws_port: int = Field(default=19190, ge=1, le=65535, description="WebSocket端口")

    def __init__(self, config: Dict[str, Any]):
        """
        初始化Warudo Provider

        Args:
            config: 配置字典，包含:
                - ws_host: WebSocket主机地址 (默认: localhost)
                - ws_port: WebSocket端口 (默认: 19190)
        """
        super().__init__(config)
        self.logger = get_logger("WarudoOutputProvider")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # WebSocket配置
        self.ws_host = self.typed_config.ws_host
        self.ws_port = self.typed_config.ws_port
        self.websocket = None
        self._is_connected = False

        self.event_bus = None

    async def setup(self, event_bus: "EventBus"):
        """
        设置Provider

        Args:
            event_bus: 事件总线实例
        """
        self.event_bus = event_bus

        # 注册事件监听
        self.event_bus.on(CoreEvents.VTS_SEND_EMOTION, self._handle_emotion)
        self.event_bus.on(CoreEvents.VTS_SEND_STATE, self._handle_state)
        self.event_bus.on(CoreEvents.TTS_AUDIO_READY, self._handle_tts_audio)

        # 启动WebSocket连接
        await self._connect_websocket()

        self.logger.info("WarudoOutputProvider 已设置")

    async def cleanup(self):
        """清理资源"""
        # 取消事件监听
        if self.event_bus:
            self.event_bus.off(CoreEvents.VTS_SEND_EMOTION, self._handle_emotion)
            self.event_bus.off(CoreEvents.VTS_SEND_STATE, self._handle_state)
            self.event_bus.off(CoreEvents.TTS_AUDIO_READY, self._handle_tts_audio)

        # 关闭WebSocket连接
        if self.websocket:
            await self.websocket.close()

        self.logger.info("WarudoOutputProvider 已清理")

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
            self._is_connected = False

    async def _handle_emotion(self, event_name: str, data: Any, source: str):
        """处理表情事件"""
        self.logger.debug(f"收到表情事件: {data}")
        # TODO: 实现实际的表情发送逻辑
        # STATUS: PENDING - Warudo API 待调研

    async def _handle_state(self, event_name: str, data: Any, source: str):
        """处理状态事件"""
        self.logger.debug(f"收到状态事件: {data}")
        # TODO: 实现实际的状态发送逻辑
        # STATUS: PENDING - Warudo API 待调研

    async def _handle_tts_audio(self, event_name: str, data: Any, source: str):
        """处理TTS音频事件"""
        self.logger.debug("收到TTS音频事件")
        # TODO: 实现实际的TTS音频同步逻辑
        # STATUS: PENDING - Warudo API 待调研
