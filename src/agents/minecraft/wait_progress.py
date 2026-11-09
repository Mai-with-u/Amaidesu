"""挂起等待目标事件期间的进展监视。

游戏 Agent 让出回合等后台目标时，身体站着不动（站桩）。这段等待里如果目标的状态
一直没有变化，就是站着干等——超过上限就叫醒 Agent，让它重新评估（继续等、取消、
换别的事做）。

叫醒只是重新评估的机会：合法长活动（游泳渡海、烧炼进行中）被叫醒后可以确认继续，
宿主不替它取消目标，也不打断它正在做的事。进展以目标状态变化为准。

时刻用注入的单调时钟（秒）；非线程安全，只在 Agent 事件循环里用。
"""

from __future__ import annotations

from typing import Callable, List, Sequence

__all__ = ["WAIT_PROGRESS_LIMIT_S", "WaitProgress"]

#: 站桩多久没有任何进展就叫醒 Agent 重新评估（秒）
WAIT_PROGRESS_LIMIT_S = 30.0


class WaitProgress:
    """站桩期间的计时与进展标记。

    站桩从"让出回合等目标事件"开始，到 Agent 醒来做事为止；这段时间里目标的状态
    变化算一次进展，重新计时。
    """

    def __init__(self, clock: Callable[[], float], limit_s: float = WAIT_PROGRESS_LIMIT_S) -> None:
        """Args:
        clock: 注入的单调时钟（秒）
        limit_s: 站桩无进展上限（秒）
        """
        self._clock = clock
        self._limit_s = limit_s
        self._standing_since: float | None = None
        self._last_progress_at: float | None = None
        self._goal_ids: List[int] = []

    @property
    def limit_s(self) -> float:
        """站桩无进展上限（秒）。"""
        return self._limit_s

    @property
    def standing(self) -> bool:
        """当前是否在站桩（让出回合等目标事件）。"""
        return self._standing_since is not None

    @property
    def goal_ids(self) -> List[int]:
        """这次站桩在等的目标编号。"""
        return list(self._goal_ids)

    def stand(self, goal_ids: Sequence[int]) -> None:
        """站桩开始（让出回合等这些目标）。"""
        now = self._clock()
        self._standing_since = now
        self._last_progress_at = now
        self._goal_ids = list(goal_ids)

    def resume(self) -> None:
        """站桩结束（Agent 醒来做事了）。"""
        self._standing_since = None
        self._last_progress_at = None
        self._goal_ids = []

    def note_progress(self) -> None:
        """目标状态变化或事件推进：重新计时（不在站桩时不做任何事）。"""
        if self._standing_since is None:
            return
        self._last_progress_at = self._clock()

    def standing_seconds(self) -> float:
        """这一段站桩了多久（秒）。"""
        if self._standing_since is None:
            return 0.0
        return max(0.0, self._clock() - self._standing_since)

    def stalled_seconds(self) -> float:
        """距上一次进展过了多久（秒）。"""
        if self._last_progress_at is None:
            return 0.0
        return max(0.0, self._clock() - self._last_progress_at)

    def remaining_seconds(self) -> float:
        """距离无进展上限还剩多久（秒；站桩中才有意义）。"""
        return max(0.0, self._limit_s - self.stalled_seconds())

    def due(self) -> bool:
        """站桩超过上限且期间没有任何进展：该叫醒 Agent 重新评估了。"""
        if self._standing_since is None:
            return False
        return self.stalled_seconds() >= self._limit_s
