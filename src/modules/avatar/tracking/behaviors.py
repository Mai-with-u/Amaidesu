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
    """自动眨眼：频率由调用方按状态给（空闲、说话、读弹幕各不同），眼皮闭合快、睁开慢，偶尔连眨两下。

    一次眨眼：约 80 毫秒闭上 → 停 40 毫秒 → 约 160 毫秒睁开。两次眨眼至少隔
    1.2 秒（不应期），间隔在不应期之后按指数分布抽取，平均值由每分钟次数决定；
    频率变化在下一次排期时生效。
    大幅转移视线时调用 :meth:`nudge`，按概率顺带眨一次（真人换注视点时常眨眼）。
    """

    CLOSE_MS = 80
    HOLD_MS = 40
    OPEN_MS = 160
    REFRACTORY_MS = 1200
    DOUBLE_CHANCE = 0.1
    DOUBLE_GAP_MS = 150

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._blink_start: Optional[int] = None
        self._next_at: Optional[int] = None
        self._last_end = -(10**12)

    @property
    def duration_ms(self) -> int:
        return self.CLOSE_MS + self.HOLD_MS + self.OPEN_MS

    def _interval_ms(self, per_min: float) -> int:
        mean = max(60_000.0 / max(per_min, 0.1), self.REFRACTORY_MS + 100.0)
        return int(self.REFRACTORY_MS + self._rng.expovariate(1.0 / (mean - self.REFRACTORY_MS)))

    def nudge(self, now_ms: int, chance: float) -> None:
        """视线大幅跳动时按概率立刻眨一次（不应期内、正在眨时不触发）。"""
        if self._blink_start is not None or now_ms - self._last_end < self.REFRACTORY_MS:
            return
        if self._rng.random() < chance:
            self._next_at = now_ms

    def closure(self, now_ms: int, *, per_min: float) -> float:
        """当前眼皮闭合量（0 = 睁开，1 = 闭上）；``per_min`` 是当前状态的平均每分钟次数。"""
        if self._next_at is None:
            # 第一次取值时随机错开起点，避免每次启动都在同一时刻眨眼
            self._next_at = now_ms + int(self._rng.uniform(0.3, 1.0) * self._interval_ms(per_min))
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
                self._next_at = now_ms + self._interval_ms(per_min)
            return 0.0
        if dt < self.CLOSE_MS:
            return dt / self.CLOSE_MS
        if dt < self.CLOSE_MS + self.HOLD_MS:
            return 1.0
        return 1.0 - (dt - self.CLOSE_MS - self.HOLD_MS) / self.OPEN_MS


class GazeGenerator:
    """眼神：围绕一个注视中心活动，三种模式。

    - **看镜头**（``CAMERA``）：中心附近每 0.6~2.5 秒细跳一次；每 7~15 秒瞥向别处
      （幅度 0.3~0.55），停 0.4~1.2 秒再看回来；
    - **盯住某处**（``FIXATE``）：只有细跳，不主动瞥视（思考时望着别处、瞥一眼弹幕区）；
    - **读弹幕**（``READING``）：一行行读——每 0.22~0.42 秒往右挪一小步，读完一行
      回到行首、下移一行，读三行再从头。

    跳动本身是瞬时的，由合成器的速度上限把它走成几十毫秒的眼跳。返回值是绝对注视点
    （中心 + 偏移），第三项标记"这一帧发生了大幅跳动"，合成器据此顺带眨眼。
    """

    CAMERA = "camera"
    FIXATE = "fixate"
    READING = "reading"
    BIG_JUMP = 0.25

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._mode = self.CAMERA
        self._center: Tuple[float, float] = (0.0, 0.0)
        self._point: Tuple[float, float] = (0.0, 0.0)
        self._next_saccade: Optional[int] = None
        self._next_glance: Optional[int] = None
        self._glance_until: Optional[int] = None
        self._read_step = 0
        self._read_line = 0
        self._pending_jump = False

    @property
    def mode(self) -> str:
        return self._mode

    def _absolute(self) -> Tuple[float, float]:
        return self._center[0] + self._point[0], self._center[1] + self._point[1]

    def set_target(self, mode: str, center: Tuple[float, float], now_ms: int) -> None:
        """换注视模式或中心（相同则不动）；换得远时下一帧标记为大幅跳动。"""
        if mode == self._mode and center == self._center:
            return
        before = self._absolute()
        self._mode = mode
        self._center = center
        self._point = (-0.12, 0.04) if mode == self.READING else (0.0, 0.0)
        self._read_step = 0
        self._read_line = 0
        self._glance_until = None
        self._next_saccade = now_ms + int(
            self._rng.uniform(220, 420) if mode == self.READING else self._rng.uniform(600, 2500)
        )
        self._next_glance = now_ms + int(self._rng.uniform(7000, 15000))
        after = self._absolute()
        self._pending_jump = math.hypot(after[0] - before[0], after[1] - before[1]) > self.BIG_JUMP

    def _move_to(self, point: Tuple[float, float]) -> bool:
        jumped = math.hypot(point[0] - self._point[0], point[1] - self._point[1]) > self.BIG_JUMP
        self._point = point
        return jumped

    def _sample_reading(self, now_ms: int) -> bool:
        if now_ms < self._next_saccade:
            return False
        self._next_saccade = now_ms + int(self._rng.uniform(220, 420))
        self._read_step += 1
        if self._read_step >= self._rng.randint(3, 5):
            # 读完一行：回到行首、下移一行；读完三行从头再来
            self._read_step = 0
            self._read_line = (self._read_line + 1) % 3
            return self._move_to((-0.12, 0.04 - 0.05 * self._read_line))
        return self._move_to((self._point[0] + self._rng.uniform(0.05, 0.09), self._point[1]))

    def sample(self, now_ms: int) -> Tuple[float, float, bool]:
        """当前绝对注视点与这一帧是否发生了大幅跳动。"""
        rng = self._rng
        if self._next_saccade is None:
            self._next_saccade = now_ms + int(rng.uniform(600, 2500))
            self._next_glance = now_ms + int(rng.uniform(7000, 15000))
        jumped, self._pending_jump = self._pending_jump, False
        if self._mode == self.READING:
            jumped = self._sample_reading(now_ms) or jumped
            x, y = self._absolute()
            return x, y, jumped
        if self._glance_until is not None:
            if now_ms >= self._glance_until:
                # 瞥完看回中心，重新排下一次瞥视
                self._glance_until = None
                self._next_glance = now_ms + int(rng.uniform(7000, 15000))
                self._next_saccade = now_ms + int(rng.uniform(600, 2500))
                jumped = self._move_to((rng.gauss(0.0, 0.05), rng.gauss(0.0, 0.03))) or jumped
            x, y = self._absolute()
            return x, y, jumped
        if self._mode == self.CAMERA and self._next_glance is not None and now_ms >= self._next_glance:
            angle = rng.uniform(0.0, 2.0 * math.pi)
            magnitude = rng.uniform(0.3, 0.55)
            self._glance_until = now_ms + int(rng.uniform(400, 1200))
            jumped = self._move_to((magnitude * math.cos(angle), 0.6 * magnitude * math.sin(angle))) or jumped
        elif now_ms >= self._next_saccade:
            self._next_saccade = now_ms + int(rng.uniform(600, 2500))
            jumped = self._move_to((rng.gauss(0.0, 0.06), rng.gauss(0.0, 0.04))) or jumped
        x, y = self._absolute()
        return x, y, jumped


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
