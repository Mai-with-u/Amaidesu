"""TimingGate - 直播节奏门控（仅保留强制触发判定）

"要不要发言"由 Planner 决定（should_reply），本类只负责单条消息的强制
触发判定（``is_forced``，按消息类型判定，如醒目留言）；批级"任一强制即
整批强制"由 MessageBuffer 逐条 add 时累积 force 标志承载。

本类为纯逻辑（无 IO），状态机由 StreamerAgent 单实例持有。
"""

from typing import List

from src.modules.events.payloads.room import RoomMessagePayload


class TimingGate:
    """直播节奏门控（精简版：仅保留强制触发判定）。"""

    def __init__(
        self,
        *,
        force_message_types: List[str],
    ) -> None:
        """
        Args:
            force_message_types: 强制触发的消息类型列表（如 super_chat / gift）
        """
        self._force_message_types = set(force_message_types)

    def is_forced(self, message: RoomMessagePayload) -> bool:
        """判断单条消息是否触发强制响应。"""
        return message.message_type in self._force_message_types
