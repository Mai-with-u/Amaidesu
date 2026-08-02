"""
TimingGate - 直播节奏门控（Task 11 精简版）

设计变更（Task 11）：
- 移除：采样率（participation_rate）/ no_action 退避（backoff_*）相关状态与逻辑。
  原因：两阶段重构（Task 10）后，"要不要发言"已由 Planner 决定（should_reply），
  节奏门控不再需要在 Decision 阶段做概率采样/退避。
- 保留：is_forced / batch_is_forced（强制触发判定，醒目留言/上舰/高 importance → forced）。
- 保留：should_act / record_result 方法签名（API 稳定性），
  但内部已退化为"恒通过"——实际节拍由 MessageBuffer + Planner 接管。

本类为纯逻辑（无 IO），状态机由 AmaidesuDecider 单实例持有。
"""

from typing import List, Tuple

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

    def should_act(self, *, forced: bool) -> Tuple[bool, str]:
        """判定本批是否应进入内容决策。

        Task 11 精简：采样/退避已移除，恒返回 True。
        实际"要不要发言"由 Planner（should_reply）裁决。

        Args:
            forced: 本批是否包含强制触发消息

        Returns:
            (True, "forced" | "proceed") —— 恒通过，仅区分原因标识便于日志/调试
        """
        if forced:
            return True, "forced"
        return True, "proceed"

    def record_result(self, *, replied: bool) -> None:
        """记录一次决策结果。

        Task 11 精简：no_action 退避已移除，本方法为 no-op。
        保留签名是为了不破坏 AmaidesuDecider 的调用点（API 稳定性）。

        Args:
            replied: 本次决策是否实际发布了发言（已被忽略）
        """
        return None
