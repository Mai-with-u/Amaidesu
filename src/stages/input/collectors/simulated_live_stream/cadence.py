"""模拟直播间节奏生成器。

负责按泊松过程生成消息到达间隔（inter-arrival time），并维护
``WARMUP`` / ``NORMAL`` / ``BURST`` / ``IDLE`` 四态机。
"""

from __future__ import annotations

import asyncio  # noqa: F401  # next_delay_seconds 为 async 方法，与 asyncio 生态保持一致
import random
import time
from typing import Optional

from src.stages.input.collectors.simulated_live_stream.config_schema import SimulatorConfigSchema
from src.stages.input.collectors.simulated_live_stream.types import BurstState


class CadenceGenerator:
    """按状态机控制模拟直播间消息生成节奏。

    状态机：

    - ``WARMUP``：启动暖场期。生成率 = ``base_rate_per_minute * 0.5``。
    - ``NORMAL``：正常运行态。生成率 = ``base_rate_per_minute``。
    - ``BURST``：突发态。生成率 = ``base_rate_per_minute * burst_multiplier``。
      持续 ``burst_cooldown_s`` 后自动回到 ``NORMAL``。
    - ``IDLE``：主播无活动空闲态。生成率 =
      ``base_rate_per_minute * idle_rate_multiplier``。

    时间追踪统一使用 :func:`time.monotonic`，RNG 支持注入 ``seed``
    以保证测试可复现。
    """

    # 状态名常量（字符串，便于跨模块传递与日志追踪）
    WARMUP: str = "WARMUP"
    NORMAL: str = "NORMAL"
    BURST: str = "BURST"
    IDLE: str = "IDLE"

    _STATES: frozenset[str] = frozenset({WARMUP, NORMAL, BURST, IDLE})

    # WARMUP 期的固定倍率（schema 未暴露，独立常量）
    _WARMUP_RATE_MULTIPLIER: float = 0.5

    def __init__(self, config: SimulatorConfigSchema, seed: Optional[int] = None) -> None:
        """初始化节奏生成器。

        Args:
            config: 模拟器配置（含各倍率与时窗）。
            seed: RNG 种子，传入后生成序列完全可复现。
        """
        self.config: SimulatorConfigSchema = config
        # 独立 RNG 实例，避免污染全局 random 状态
        self._rng: random.Random = random.Random(seed) if seed is not None else random.Random()

        now: float = time.monotonic()
        self._started_at: float = now
        self._last_streamer_activity_at: float = now
        self._last_burst_at: float = -float("inf")
        self._started_burst_at: float = -float("inf")

        self._state: str = self.WARMUP

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def get_state(self) -> str:
        """返回当前状态名。"""
        return self._state

    def update_config(self, config: SimulatorConfigSchema) -> None:
        """运行时热更新配置（Dashboard 调参场景）。"""
        self.config = config

    def get_burst_state(self) -> BurstState:
        """返回当前 ``BURST`` 状态的快照。"""
        return BurstState(
            is_active=self._state == self.BURST,
            started_at_ms=int(self._started_burst_at * 1000) if self._started_burst_at != -float("inf") else 0,
            last_triggered_at_ms=int(self._last_burst_at * 1000) if self._last_burst_at != -float("inf") else 0,
        )

    def notify_streamer_activity(self) -> None:
        """通知节奏生成器：主播产生了新活动。

        行为：
        - 刷新 ``_last_streamer_activity_at`` 时间戳；
        - 若当前处于 ``IDLE``，立即唤醒到 ``NORMAL``。
        """
        now: float = time.monotonic()
        self._last_streamer_activity_at = now
        if self._state == self.IDLE:
            self._state = self.NORMAL

    def trigger_burst(self) -> bool:
        """尝试触发突发模式。

        Returns:
            True 表示成功进入 ``BURST``；False 表示被守卫拦截
            （当前处于 ``IDLE``，或距上次触发未满 ``burst_min_interval_s``）。
        """
        # IDLE 状态下不允许直接触发；需先通过 notify_streamer_activity 唤醒
        if self._state == self.IDLE:
            return False

        now: float = time.monotonic()
        # 最小间隔守卫
        if now - self._last_burst_at < self.config.burst_min_interval_s:
            return False

        self._state = self.BURST
        self._last_burst_at = now
        self._started_burst_at = now
        return True

    async def next_delay_seconds(self) -> float:
        """计算下一条消息前需要等待的秒数。

        根据 ``config.cadence_mode`` 选择算法：
        - ``uniform``：有界均匀分布，范围 [mean*0.3, mean*2.0]
        - ``fixed``：固定间隔 ``fixed_interval_s``
        - ``auto``：uniform 基础上再叠加突发（由外部 trigger_burst 驱动）
        """
        self._advance_state(time.monotonic())

        mode = self.config.cadence_mode
        if mode == "fixed":
            return self.config.fixed_interval_s

        # uniform / auto 共用有界均匀
        rate_per_min: float = self._effective_rate_per_minute(self._state)
        if rate_per_min <= 0.0:
            return float("inf")

        mean_interval_s: float = 60.0 / rate_per_min
        lo: float = max(mean_interval_s * 0.3, 1.0)
        hi: float = mean_interval_s * 2.0
        return self._rng.uniform(lo, hi)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _advance_state(self, now: float) -> None:
        """按当前时间推进状态机。

        转换规则：
        - ``WARMUP`` 持续 ``warmup_duration_s`` 后 → ``NORMAL``。
        - ``BURST`` 持续 ``burst_cooldown_s`` 后 → ``NORMAL``。
        - ``NORMAL`` / ``BURST`` 距上次主播活动超过 ``idle_threshold_s``
          → ``IDLE``（``IDLE`` 状态下不再做 IDLE 检测，避免反复触发）。

        ``IDLE`` 仅能通过 :meth:`notify_streamer_activity` 唤醒。
        """
        if self._state == self.WARMUP:
            if now - self._started_at >= self.config.warmup_duration_s:
                self._state = self.NORMAL
                return

        if self._state == self.BURST:
            if now - self._started_burst_at >= self.config.burst_cooldown_s:
                self._state = self.NORMAL
                return

        if self._state in (self.NORMAL, self.BURST):
            if now - self._last_streamer_activity_at >= self.config.idle_threshold_s:
                self._state = self.IDLE
                return

    def _effective_rate_per_minute(self, state: str) -> float:
        """根据状态返回每分钟有效生成率。"""
        base: float = self.config.base_rate_per_minute
        if state == self.WARMUP:
            return base * self._WARMUP_RATE_MULTIPLIER
        if state == self.NORMAL:
            return base
        if state == self.BURST:
            return base * self.config.burst_multiplier
        if state == self.IDLE:
            return base * self.config.idle_rate_multiplier
        # 防御性兜底：理论上不应到达
        return base


__all__ = ["CadenceGenerator"]
