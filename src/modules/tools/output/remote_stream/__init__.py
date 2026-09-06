"""远程流消息协议模块

消息协议定义（前后端共享）+ 消息分发器；websocket 传输由独立 transport 层负责。

- ``MessageType`` / ``StreamMessage`` / ``AudioConfig`` / ``ImageConfig``
- ``RemoteStreamTypes.dispatch_message`` 消息分发
"""

from .remote_stream_types import (
    AudioConfig,
    ImageConfig,
    MessageType,
    RemoteStreamTypes,
    StreamMessage,
)

__all__ = [
    "MessageType",
    "StreamMessage",
    "AudioConfig",
    "ImageConfig",
    "RemoteStreamTypes",
]
