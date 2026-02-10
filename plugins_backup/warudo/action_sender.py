import json
from typing import Optional

import websockets

debug_log_actions = [
    "eye_shift_left",
    "eye_shift_right",
    "eye_close",
]


class ActionSender:
    def __init__(self, websocket: Optional[websockets.WebSocketClientProtocol] = None):
        self.websocket = websocket

    def set_websocket(self, websocket: websockets.WebSocketClientProtocol):
        """设置WebSocket连接"""
        self.websocket = websocket

    async def send_action(self, action: str, data):
        if not self.websocket:
            return

        # 如果data已经是字典格式（包含action和data字段），直接使用
        if isinstance(data, dict) and "action" in data and "data" in data:
            action_message = data
        else:
            # 否则使用原来的格式
            action_message = {"action": action, "data": data}
        try:
            await self.websocket.send(json.dumps(action_message))
        except Exception as e:
            print(f"发送动作失败: {e}")


# 全局ActionSender实例
action_sender = ActionSender()
