"""TimingGate - 直播节奏门控（仅保留强制触发判定）

"要不要发言"由 Planner 决定（should_reply），本类只负责强制触发判定：
``is_forced`` / ``batch_is_forced``（醒目留言/上舰/高 importance → forced）。

本类为纯逻辑（无 IO），状态机由 StreamerAgent 单实例持有。
"""

from typing import List

from src.modules.types.base.normalized_message import NormalizedMessage


class TimingGate:
    """直播节奏门控（精简版：仅保留强制触发判定）。"""

    def __init__(
        self,
        *,
        force_data_types: List[str],
        force_importance: float,
    ) -> None:
        """
        Args:
            force_data_types: 强制触发的数据类型列表（如 super_chat / guard）
            force_importance: importance 达到该值则强制触发
        """
        self._force_data_types = set(force_data_types)
        self._force_importance = force_importance

    def is_forced(self, message: NormalizedMessage) -> bool:
        """判断单条消息是否触发强制响应。"""
        if message.data_type in self._force_data_types:
            return True
        if message.importance >= self._force_importance:
            return True
        return False

    def batch_is_forced(self, messages: List[NormalizedMessage]) -> bool:
        """判断一批消息中是否存在强制触发消息。"""
        return any(self.is_forced(message) for message in messages)
