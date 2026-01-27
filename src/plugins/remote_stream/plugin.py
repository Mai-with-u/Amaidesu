#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Amaidesu Remote Stream Plugin - 远程流媒体插件（新架构）

通过WebSocket协议实现与边缘设备的音视频双向传输。
"""

import asyncio
import base64
import json
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, asdict
from enum import Enum

from src.core.plugin import Plugin
from src.core.event_bus import EventBus
from src.utils.logger import get_logger

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    websockets = None
    WebSocketServerProtocol = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    from PIL import Image
except ImportError:
    Image = None


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


class RemoteStreamPlugin(Plugin):
    """远程流媒体插件（新架构）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.event_bus = None
        self.logger = get_logger("RemoteStreamPlugin")

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

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]) -> list:
        """初始化插件"""
        self.event_bus = event_bus
        self.config = config

        # 检查依赖
        if websockets is None:
            self.logger.error("websockets库未安装")
            return []

        # 注册事件监听
        self.event_bus.on("remote_stream.request_image", self._handle_image_request)
        self.event_bus.on("remote_stream.send_tts", self._handle_tts_request)

        self.logger.info("RemoteStreamPlugin 初始化完成")
        return []

    async def cleanup(self):
        """清理资源"""
        if self.server:
            self.server.close()

        if self.client:
            await self.client.close()

        if self.event_bus:
            self.event_bus.off("remote_stream.request_image", self._handle_image_request)
            self.event_bus.off("remote_stream.send_tts", self._handle_tts_request)

        self.logger.info("RemoteStreamPlugin 清理完成")

    def register_audio_callback(self, event: str, callback: Callable):
        if event in self.audio_callbacks:
            self.audio_callbacks[event].append(callback)

    def register_image_callback(self, event: str, callback: Callable):
        if event in self.image_callbacks:
            self.image_callbacks[event].append(callback)

    async def _handle_image_request(self, event_name: str, data: Any, source: str):
        """处理图像请求事件"""
        self.logger.debug(f"收到图像请求: {data}")

    async def _handle_tts_request(self, event_name: str, data: Any, source: str):
        """处理TTS发送请求事件"""
        self.logger.debug(f"收到TTS发送请求: {data}")

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "RemoteStream",
            "version": "2.0.0",
            "author": "Amaidesu Team",
            "description": "远程流媒体插件 - 音视频双向传输",
            "category": "hardware",
            "api_version": "2.0",
        }


plugin_entrypoint = RemoteStreamPlugin
