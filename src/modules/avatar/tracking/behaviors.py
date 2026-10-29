"""面捕合成器的反射类行为：眨眼、眼神微动、头部漫步

这些都是快的、不需要 LLM 决定的身体过程，各自只管一件事、只靠注入的时刻与随机源
推进（同一个种子 + 同一串时刻 = 同一串输出），合成器每帧向它们取当前值。
"""

from __future__ import annotations

import math
import random
from typing import Optional, Tuple


def smootherstep(x: float) -> float:
    """两端速度与加速度都为 0 的缓动曲线（0~1），用于起落与漫步，运动无顿挫。"""
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (x * 6 - 15) + 10)


class BlinkGenerator:
    """自动眨眼：频率随说话与否变化，眼皮闭合快、睁开慢，偶尔连眨两下。

    一次眨眼：约 80 毫秒闭上 → 停 40 毫秒 → 约 160 毫秒睁开。两次眨眼至少隔
    1.2 秒（不应期），间隔在不应期之后按指数分布抽取，平均值由每分钟次数决定。
    大幅转移视线时调用 :meth:`nudge`，按概率顺带眨一次（真人换注视点时常眨眼）。
    """

    CLOSE_MS = 80
    HOLD_MS = 40
    OPEN_MS = 160
    REFRACTORY_MS = 1200
    DOUBLE_CHANCE = 0.1
    DOUBLE_GAP_MS = 150

    def __init__(self, rng: random.Random, *, idle_per_min: float, speaking_per_min: float) -> None:
        self._rng = rng
        self._idle_per_min = idle_per_min
        self._speaking_per_min = speaking_per_min
        self._blink_start: Optional[int] = None
        self._next_at: Optional[int] = None
        self._last_end = -(10**12)

    @property
    def duration_ms(self) -> int:
        return self.CLOSE_MS + self.HOLD_MS + self.OPEN_MS

    def _interval_ms(self, speaking: bool) -> int:
        per_min = self._speaking_per_min if speaking else self._idle_per_min
        mean = max(60_000.0 / per_min, self.REFRACTORY_MS + 100.0)
        return int(self.REFRACTORY_MS + self._rng.expovariate(1.0 / (mean - self.REFRACTORY_MS)))

    def nudge(self, now_ms: int, chance: float) -> None:
        """视线大幅跳动时按概率立刻眨一次（不应期内、正在眨时不触发）。"""
        if self._blink_start is not None or now_ms - self._last_end < self.REFRACTORY_MS:
            return
        if self._rng.random() < chance:
            self._next_at = now_ms

    def closure(self, now_ms: int, *, speaking: bool) -> float:
        """当前眼皮闭合量（0 = 睁开，1 = 闭上）。"""
        if self._next_at is None:
            # 第一次取值时随机错开起点，避免每次启动都在同一时刻眨眼
            self._next_at = now_ms + int(self._rng.uniform(0.3, 1.0) * self._interval_ms(speaking))
        if self._blink_start is None and now_ms >= self._next_at:
            # 从排定时刻起算闭合进度（帧间隔再粗也不丢相位）；排定时刻已过去一整次眨眼
            # 那么久（比如进程卡住）就从现在起眨，不吞掉这一次
            late = now_ms - self._next_at >= self.duration_ms
            self._blink_start = now_ms if late else self._next_at
        if self._blink_start is None:
            return 0.0
        dt = now_ms - self._blink_start
        if dt >= self.duration_ms:
            # 一次眨眼结束：排下一次；小概率紧接着再眨一次
            self._blink_start = None
            self._last_end = now_ms
            if self._rng.random() < self.DOUBLE_CHANCE:
                self._next_at = now_ms + self.DOUBLE_GAP_MS
            else:
                self._next_at = now_ms + self._interval_ms(speaking)
            return 0.0
        if dt < self.CLOSE_MS:
            return dt / self.CLOSE_MS
        if dt < self.CLOSE_MS + self.HOLD_MS:
            return 1.0
        return 1.0 - (dt - self.CLOSE_MS - self.HOLD_MS) / self.OPEN_MS


class GazeGenerator:
    """眼神：盯着注视中心时有细小的跳动，隔一阵瞥向别处，停一会儿再看回来。

    细小跳动每 0.6~2.5 秒一次，落点在中心附近；瞥视每 7~15 秒一次，幅度 0.3~0.55，
    停 0.4~1.2 秒后回到中心。跳动本身是瞬时的，由合成器的速度上限把它走成几十毫秒
    的眼跳。返回值第三项标记"这一帧发生了大幅跳动"，合成器据此顺带眨眼。
    """

    BIG_JUMP = 0.25

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._point: Tuple[float, float] = (0.0, 0.0)
        self._next_saccade: Optional[int] = None
        self._next_glance: Optional[int] = None
        self._glance_until: Optional[int] = None

    def _move_to(self, point: Tuple[float, float]) -> bool:
        jumped = math.hypot(point[0] - self._point[0], point[1] - self._point[1]) > self.BIG_JUMP
        self._point = point
        return jumped

    def sample(self, now_ms: int) -> Tuple[float, float, bool]:
        """当前注视点（相对注视中心）与这一帧是否发生了大幅跳动。"""
        rng = self._rng
        if self._next_saccade is None:
            self._next_saccade = now_ms + int(rng.uniform(600, 2500))
            self._next_glance = now_ms + int(rng.uniform(7000, 15000))
        jumped = False
        if self._glance_until is not None:
            if now_ms >= self._glance_until:
                # 瞥完看回中心，重新排下一次瞥视
                self._glance_until = None
                self._next_glance = now_ms + int(rng.uniform(7000, 15000))
                self._next_saccade = now_ms + int(rng.uniform(600, 2500))
                jumped = self._move_to((rng.gauss(0.0, 0.05), rng.gauss(0.0, 0.03)))
            return self._point[0], self._point[1], jumped
        if self._next_glance is not None and now_ms >= self._next_glance:
            angle = rng.uniform(0.0, 2.0 * math.pi)
            magnitude = rng.uniform(0.3, 0.55)
            self._glance_until = now_ms + int(rng.uniform(400, 1200))
            jumped = self._move_to((magnitude * math.cos(angle), 0.6 * magnitude * math.sin(angle)))
        elif now_ms >= self._next_saccade:
            self._next_saccade = now_ms + int(rng.uniform(600, 2500))
            jumped = self._move_to((rng.gauss(0.0, 0.06), rng.gauss(0.0, 0.04)))
        return self._point[0], self._point[1], jumped


class AxisWander:
    """单轴随机漫步：随机目标 + 随机时长缓动过去，到位后随机停一会儿或接着走。

    目标偏向中心（三角分布），约两成概率来一次大幅度；输出约 -1~1 的归一化值。
    比固定频率的正弦更像真人的微动作（节奏、时长、幅度都不规则）。
    """

    def __init__(
        self,
        rng: random.Random,
        *,
        min_duration_ms: int,
        max_duration_ms: int,
        pause_probability: float,
        max_pause_ms: int,
        min_target: float = 0.3,
    ) -> None:
        self._rng = rng
        self._min_dur = min_duration_ms
        self._max_dur = max_duration_ms
        self._pause_prob = pause_probability
        self._max_pause = max_pause_ms
        self._min_target = min_target
        self._from = 0.0
        self._to = 0.0
        self._seg_start = 0
        self._seg_dur = 1000
        self._pause_until: Optional[int] = None
        self._initialized = False

    def _new_target(self) -> float:
        if self._rng.random() < 0.2:
            mag = self._rng.uniform(0.7, 1.0)
        else:
            mag = abs(self._rng.triangular(-1.0, 1.0, 0.0))
        mag = max(mag, self._min_target)
        return mag if self._rng.random() < 0.5 else -mag

    def _new_segment(self, now_ms: int) -> None:
        self._from = self._to
        self._to = self._new_target()
        self._seg_start = now_ms
        self._seg_dur = int(self._rng.uniform(self._min_dur, self._max_dur))

    def value(self, now_ms: int) -> float:
        """当前时刻的归一化输出。"""
        if not self._initialized:
            # 随机化初始进度，几个轴不会同时从 0 起步
            self._initialized = True
            self._new_segment(now_ms)
            self._seg_start = now_ms - int(self._rng.uniform(0, self._seg_dur))
        if self._pause_until is not None:
            if now_ms < self._pause_until:
                return self._to
            self._pause_until = None
            self._new_segment(now_ms)
            return self._from
        x = (now_ms - self._seg_start) / max(self._seg_dur, 50)
        if x >= 1.0:
            # 走到目标：按概率原地停一会儿，否则马上走向下一个目标；这一帧都停在目标上
            arrived = self._to
            if self._rng.random() < self._pause_prob:
                self._pause_until = now_ms + int(self._rng.uniform(300, self._max_pause))
            else:
                self._new_segment(now_ms)
            return arrived
        return self._from + (self._to - self._from) * smootherstep(x)


class HeadWander:
    """头部三轴漫步（左右转、上下点、左右歪），输出角度；点头轴幅度小一些。"""

    def __init__(self, rng: random.Random) -> None:
        self._yaw = AxisWander(
            rng, min_duration_ms=1800, max_duration_ms=4500, pause_probability=0.35, max_pause_ms=2500
        )
        self._pitch = AxisWander(
            rng, min_duration_ms=2000, max_duration_ms=5000, pause_probability=0.4, max_pause_ms=3000
        )
        self._roll = AxisWander(
            rng, min_duration_ms=2500, max_duration_ms=6000, pause_probability=0.4, max_pause_ms=3000
        )

    def sample(self, now_ms: int, amplitude_deg: float) -> Tuple[float, float, float]:
        """当前 (左右转, 上下点, 左右歪) 角度。"""
        return (
            self._yaw.value(now_ms) * amplitude_deg,
            self._pitch.value(now_ms) * amplitude_deg * 0.6,
            self._roll.value(now_ms) * amplitude_deg * 0.8,
        )


__all__ = ["AxisWander", "BlinkGenerator", "GazeGenerator", "HeadWander", "smootherstep"]
