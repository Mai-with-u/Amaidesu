# Bilibili 官方 WebSocket 客户端（v2 迁移）
# 迁移自 src/stages/input/collectors/bili_danmaku_official/client/
# 协议层 verbatim —— 仅迁移位置与 import 路径
from .proto import Proto
from .websocket_client import BiliWebSocketClient

__all__ = ["BiliWebSocketClient", "Proto"]
