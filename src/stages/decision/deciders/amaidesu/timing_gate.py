"""
TimingGate - 直播节奏门控

借鉴 MaiBot Maisaka 的"要不要参与"控制，但用规则实现以适配直播低延迟：
- 强制触发：醒目留言/上舰等高价值消息，或 importance 超阈值 → 必定响应
- 采样率：普通弹幕按 participation_rate 概率性采样（类比 MaiBot 的 talk_value）
- no_action 退避：连续不发言后指数退避，避免冷场时频繁空转

本类为纯逻辑（无 IO），状态机由 AmaidesuDecider 单实例持有。
"""

import math
from typing import List, Tuple

from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.time_utils import now_ms


class TimingGate:
    """直播节奏门控状态机。"""

    def __init__(
        self,
        *,
        participation_rate: float,
        force_data_types: List[str],
        force_importance: float,
        backoff_base_ms: int,
        backoff_cap_ms: int,
    ) -> None:
        """
        Args:
            participation_rate: 普通弹幕采样率（0-1），0 表示静默（仅强制触发响应）
            force_data_types: 强制触发的数据类型列表（如 super_chat / guard）
            force_importance: importance 达到该值则强制触发
            backoff_base_ms: no_action 退避基数（毫秒）
            backoff_cap_ms: no_action 退避上限（毫秒）
        """
        self._participation_rate = participation_rate
        self._force_data_types = set(force_data_types)
        self._force_importance = force_importance
        self._backoff_base_ms = backoff_base_ms
        self._backoff_cap_ms = backoff_cap_ms

        self._sample_counter = 0
        self._no_action_count = 0
        self._backoff_until_ms = 0

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

        Args:
            forced: 本批是否包含强制触发消息

        Returns:
            (是否决策, 原因标识)
        """
        if forced:
            return True, "forced"

        if now_ms() < self._backoff_until_ms:
            return False, "backoff"

        if self._participation_rate <= 0:
            return False, "rate_zero"

        self._sample_counter += 1
        threshold = max(1, math.ceil(1.0 / self._participation_rate))
        if self._sample_counter >= threshold:
            self._sample_counter = 0
            return True, "sampled"
        return False, "sampling_skip"

    def record_result(self, *, replied: bool) -> None:
        """记录一次决策结果，更新退避状态。

        Args:
            replied: 本次决策是否实际发布了发言（reply）。
                未发言（no_action）会累计退避；发言则重置退避。
        """
        if replied:
            self._no_action_count = 0
            self._backoff_until_ms = 0
            return

        self._no_action_count += 1
        backoff_ms = min(
            self._backoff_base_ms * (2 ** (self._no_action_count - 1)),
            self._backoff_cap_ms,
        )
        self._backoff_until_ms = now_ms() + backoff_ms

    @property
    def no_action_count(self) -> int:
        """当前连续 no_action 次数（统计/调试用）"""
        return self._no_action_count
