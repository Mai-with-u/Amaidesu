"""
RemoteStreamTypes - 远程流消息协议与配置（Wave 4 拆分）

迁移自 ``src.stages.output.handlers.remote_stream.RemoteStreamHandler``：

- **保留**：``MessageType`` / ``StreamMessage`` / ``AudioConfig`` / ``ImageConfig``
  + 消息分发器（``_process_message``）
- **删除**：websocket 服务端/客户端脚手架、``OUTPUT_OBS_COMMAND`` 事件订阅

新架构下，远程流传输由独立 transport 层负责，本模块仅作为：
- 消息协议定义（前后端共享）
- 消息处理工具（解析 incoming messages）
- AudioConfig / ImageConfig（前后端协商格式）

迁移策略（与 .omo/drafts/amaidesu-v2-migration.md A 段对齐）:
- 70% 是 websocket 脚手架——新架构的 transport 由其他模块负责
- 保留数据形状（MessageType / StreamMessage / AudioConfig / ImageConfig）
- 保留消息分发器（``dispatch_message`` 工具函数）
"""

from __future__ import annotations

import base64
import io
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel


class MessageType(str, Enum):
    """WebSocket 消息类型"""

    HELLO = "hello"
    CONFIG = "config"
    AUDIO_DATA = "audio_data"
    AUDIO_REQUEST = "audio_req"
    IMAGE_DATA = "image_data"
    IMAGE_REQUEST = "image_req"
    TTS_DATA = "tts_data"
    STATUS = "status"
    ERROR = "error"


class StreamMessage(BaseModel):
    """通用 WebSocket 消息结构"""

    type: str
    data: Dict[str, Any]
    timestamp: float = 0.0
    sequence: int = 0

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, json_str: str) -> "StreamMessage":
        """从 JSON 字符串创建消息对象"""
        return cls.model_validate_json(json_str)


class AudioConfig(BaseModel):
    """音频配置"""

    sample_rate: int = 16000
    channels: int = 1
    format: str = "int16"
    chunk_size: int = 1024


class ImageConfig(BaseModel):
    """图像配置"""

    width: int = 640
    height: int = 480
    format: str = "jpeg"
    quality: int = 80


class RemoteStreamTypes:
    """远程流消息工具集合（无状态）"""

    @staticmethod
    def build_config_message(audio: AudioConfig, image: ImageConfig) -> StreamMessage:
        """构造 CONFIG 消息"""
        return StreamMessage(
            type=MessageType.CONFIG,
            data={"audio": audio.model_dump(), "image": image.model_dump()},
        )

    @staticmethod
    def build_hello_message(client_info: str) -> StreamMessage:
        """构造 HELLO 消息"""
        return StreamMessage(type=MessageType.HELLO, data={"client_info": client_info})

    @staticmethod
    def build_image_request() -> StreamMessage:
        """构造 IMAGE_REQUEST 消息"""
        return StreamMessage(type=MessageType.IMAGE_REQUEST, data={"timestamp": time.time()})

    @staticmethod
    def build_tts_data(audio: bytes, format_info: Optional[Dict[str, Any]] = None) -> StreamMessage:
        """构造 TTS_DATA 消息（base64 编码）"""
        if format_info is None:
            format_info = {"format": "int16"}
        encoded_data = base64.b64encode(audio).decode("utf-8")
        return StreamMessage(
            type=MessageType.TTS_DATA,
            data={"audio": encoded_data, "format": format_info},
        )

    @staticmethod
    def dispatch_message(
        message: StreamMessage,
        *,
        on_hello: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_config: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_audio_data: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_image_data: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_image_request: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_tts_data: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_status: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_error: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        """消息分发器（同步版本，移植自 RemoteStreamHandler._process_message）

        根据 ``message.type`` 调用对应的回调；未匹配的回调忽略。
        """
        mt = message.type

        if mt == MessageType.HELLO:
            if on_hello:
                on_hello(message.data)
            return
        if mt == MessageType.CONFIG:
            if on_config:
                audio_data = message.data.get("audio", {})
                image_data = message.data.get("image", {})
                if audio_data:
                    try:
                        message.data["audio"] = AudioConfig(**audio_data).model_dump()
                    except Exception:
                        pass
                if image_data:
                    try:
                        message.data["image"] = ImageConfig(**image_data).model_dump()
                    except Exception:
                        pass
                on_config(message.data)
            return
        if mt == MessageType.AUDIO_DATA:
            if on_audio_data:
                if "audio" in message.data and isinstance(message.data["audio"], str):
                    try:
                        binary_data = base64.b64decode(message.data["audio"])
                        message.data["binary"] = binary_data
                    except Exception:
                        pass
                on_audio_data(message.data)
            return
        if mt == MessageType.IMAGE_DATA:
            if on_image_data:
                if "image" in message.data and isinstance(message.data["image"], str):
                    try:
                        binary_data = base64.b64decode(message.data["image"])
                        message.data["binary"] = binary_data
                        try:
                            from PIL import Image as _PILImage

                            message.data["pil_image"] = _PILImage.open(io.BytesIO(binary_data))
                        except Exception:
                            pass
                    except Exception:
                        pass
                on_image_data(message.data)
            return
        if mt == MessageType.IMAGE_REQUEST:
            if on_image_request:
                on_image_request(message.data)
            return
        if mt == MessageType.TTS_DATA:
            if on_tts_data:
                on_tts_data(message.data)
            return
        if mt == MessageType.STATUS:
            if on_status:
                on_status(message.data)
            return
        if mt == MessageType.ERROR:
            if on_error:
                on_error(message.data)
            return


__all__ = [
    "MessageType",
    "StreamMessage",
    "AudioConfig",
    "ImageConfig",
    "RemoteStreamTypes",
]
