"""
Warudo ActionSender - WebSocket 动作发送器

发送动作到 Warudo WebSocket 服务器。
- 接收 WebSocket 实例(可动态切换)
- 自动检测连接状态
- 支持 dict 包装格式透传
"""

import json
from typing import Any, Optional

from src.modules.logging import get_logger

_logger = get_logger("WarudoActionSender")


class ActionSender:
    """Warudo 动作发送器

    作为实例注入到 WarudoHandler 中(避免每次调用都新建实例)。
    """

    def __init__(self, websocket: Optional[Any] = None):
        self.websocket = websocket

    def set_websocket(self, websocket: Any) -> None:
        """更新 WebSocket 连接"""
        self.websocket = websocket

    def is_ready(self) -> bool:
        """检查 WebSocket 是否就绪(兼容 websockets >= 13)"""
        if self.websocket is None:
            return False
        # websockets >= 13: 用 close_code; < 13: 用 closed
        code = getattr(self.websocket, "close_code", None)
        if code is not None:
            return False
        if hasattr(self.websocket, "closed") and self.websocket.closed:
            return False
        return True

    async def send_action(self, action: str, data: Any) -> bool:
        """
        发送动作到 Warudo

        Args:
            action: 动作名称
            data: 动作数据(可以是 dict/str/float/任何)

        Returns:
            True if sent, False if not ready or failed
        """
        ws = self.websocket
        if ws is None or (getattr(ws, "closed", False) is True):
            _logger.debug(f"ActionSender 跳过发送 {action}: WebSocket 未就绪")
            return False

        if isinstance(data, dict) and "action" in data and "data" in data:
            action_message = data
        else:
            action_message = {"action": action, "data": data}

        try:
            await ws.send(json.dumps(action_message, ensure_ascii=False))
            return True
        except Exception as e:
            _logger.error(f"发送动作失败: {e}")
            return False
