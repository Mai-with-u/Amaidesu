"""远程流消息协议模块（Wave 4 拆分）

迁移自 ``src.stages.output.handlers.remote_stream.RemoteStreamHandler``：

- **保留**：``MessageType`` / ``StreamMessage`` / ``AudioConfig`` / ``ImageConfig``
  + 消息分发器（``RemoteStreamTypes.dispatch_message``）
- **删除**：websocket 服务端/客户端脚手架（70% 是脚手架，新架构
  由 transport 层负责）、``OUTPUT_OBS_COMMAND`` 事件订阅
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
