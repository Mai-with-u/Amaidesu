"""
RemoteStream Output Provider - 远程流媒体输出Provider

职责:
- 通过WebSocket实现与边缘设备的音视频双向传输
- 处理图像请求和TTS发送请求
"""

import json
from typing import Dict, Any, List, Callable
from dataclasses import dataclass, asdict
from enum import Enum

from src.core.base.output_provider import OutputProvider
from src.core.base.base import RenderParameters
from src.core.utils.logger import get_logger

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    websockets = None
    WebSocketServerProtocol = None


class MessageType(str, Enum):
    HELLO = "hello"
    CONFIG = "config"
    AUDIO_DATA = "audio_data"
    AUDIO_REQUEST = "audio_req"
    IMAGE_DATA = "image_data"
    IMAGE_REQUEST = "image_req"
    TTS_DATA = "tts_data"
    STATUS = "status"
    ERROR = "error"


@dataclass
class StreamMessage:
    type: str
    data: Dict[str, Any]
    timestamp: float = 0.0
    sequence: int = 0

    def __post_init__(self):
        if self.timestamp == 0.0:
            import time

            self.timestamp = time.time()

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, json_str: str) -> "StreamMessage":
        data = json.loads(json_str)
        return cls(**data)


class RemoteStreamOutputProvider(OutputProvider):
    """远程流媒体输出Provider"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化RemoteStream输出Provider

        Args:
            config: 配置字典，包含:
                - server_mode: 服务器模式 (默认: True)
                - host: 主机地址 (默认: "0.0.0.0" 或 "127.0.0.1")
                - port: 端口 (默认: 8765)
                - audio: 音频配置
                - image: 图像配置
        """
        super().__init__(config)
        self.logger = get_logger("RemoteStreamOutputProvider")

        # WebSocket配置
        self.server_mode = config.get("server_mode", True)
        self.host = config.get("host", "0.0.0.0" if self.server_mode else "127.0.0.1")
        self.port = config.get("port", 8765)

        # 音频/图像配置
        self.audio_config = config.get("audio", {})
        self.image_config = config.get("image", {})

        # WebSocket连接
        self.server = None
        self.client = None
        self.connections = set()
        self._is_connected = False

        # 回调注册
        self.audio_callbacks: Dict[str, List[Callable]] = {"data": []}
        self.image_callbacks: Dict[str, List[Callable]] = {"data": []}

    async def _setup_internal(self):
        """内部设置逻辑"""
        # 检查依赖
        if websockets is None:
            self.logger.error("websockets库未安装，请使用 'uv add websockets' 安装")
            return

        # 注册事件监听
        if self.event_bus:
            self.event_bus.on("remote_stream.request_image", self._handle_image_request)
            self.event_bus.on("remote_stream.send_tts", self._handle_tts_request)

        self.logger.info("RemoteStreamOutputProvider 已设置")

    async def _render_internal(self, parameters: RenderParameters):
        """
        内部渲染逻辑

        Args:
            parameters: 渲染参数
        """
        # TODO: 实现实际的渲染逻辑
        self.logger.debug(f"渲染参数: {parameters}")

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        # 关闭WebSocket连接
        if self.server:
            self.server.close()

        if self.client:
            await self.client.close()

        # 取消事件监听
        if self.event_bus:
            self.event_bus.off("remote_stream.request_image", self._handle_image_request)
            self.event_bus.off("remote_stream.send_tts", self._handle_tts_request)

        self.logger.info("RemoteStreamOutputProvider 已清理")

    def register_audio_callback(self, event: str, callback: Callable):
        """注册音频回调"""
        if event in self.audio_callbacks:
            self.audio_callbacks[event].append(callback)

    def register_image_callback(self, event: str, callback: Callable):
        """注册图像回调"""
        if event in self.image_callbacks:
            self.image_callbacks[event].append(callback)

    async def _handle_image_request(self, event_name: str, data: Any, source: str):
        """处理图像请求事件"""
        self.logger.debug(f"收到图像请求: {data}")
        # TODO: 实现实际的图像请求处理逻辑

    async def _handle_tts_request(self, event_name: str, data: Any, source: str):
        """处理TTS发送请求事件"""
        self.logger.debug(f"收到TTS发送请求: {data}")
        # TODO: 实现实际的TTS发送逻辑

    def get_info(self) -> Dict[str, Any]:
        """
        获取Provider信息

        Returns:
            Provider信息字典
        """
        return {
            "name": self.__class__.__name__,
            "is_setup": self.is_setup,
            "type": "output_provider",
            "server_mode": self.server_mode,
            "host": self.host,
            "port": self.port,
        }
