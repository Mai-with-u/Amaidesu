"""Token 预算控制器

滑动窗口（1 小时）token 用量跟踪，两级阈值告警。

集成点：在 SimulatorLLMWrapper 内部每次成功调用后调用 record_usage()，
在 generate_* 入口检查 is_budget_exceeded()。
"""

import time
from collections import deque
from typing import Tuple


class TokenBudgetController:
    """Token 预算控制器

    维护 1 小时滑动窗口的 token 使用记录，提供两级阈值：
    - 软警告（80%）：is_budget_warning()
    - 硬上限（100%）：is_budget_exceeded()
    """

    def __init__(self, budget_per_hour: int = 50000):
        self._budget = budget_per_hour
        self._usage: "deque[Tuple[float, int]]" = deque()

    def record_usage(self, tokens: int) -> None:
        """记录一次 token 使用"""
        if tokens <= 0:
            return
        now = time.time()
        self._cleanup(now)
        self._usage.append((now, tokens))

    def is_budget_exceeded(self) -> bool:
        """是否超出硬上限（100%）"""
        return self.get_usage_last_hour() >= self._budget

    def is_budget_warning(self) -> bool:
        """是否达到软警告阈值（80%）"""
        return self.get_usage_last_hour() >= self._budget * 0.8

    def get_usage_last_hour(self) -> int:
        """过去 1 小时内的 token 使用总量"""
        self._cleanup(time.time())
        return sum(t for _, t in self._usage)

    def get_remaining(self) -> int:
        """剩余可用 token 配额"""
        return max(0, self._budget - self.get_usage_last_hour())

    def reset(self) -> None:
        """重置所有用量记录"""
        self._usage.clear()

    def _cleanup(self, now: float) -> None:
        """清理超过 1 小时的旧记录"""
        cutoff = now - 3600.0
        while self._usage and self._usage[0][0] < cutoff:
            self._usage.popleft()
