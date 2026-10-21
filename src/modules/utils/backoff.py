"""指数退避等待助手。

按 ``min(base_delay_ms * factor ** n, cap_delay_ms)`` 计算逐次等待时长，
n 为自上次 ``reset()`` 以来的调用序号（0 起）。供重连/恢复类循环消费，
无 IO、无全局状态。

时间单位遵循项目约定：毫秒 int，时长命名带 ``_ms`` 后缀。

不含 jitter（抖动）：现有消费点均无防惊群需求，没有真实需求不开这个口子。
"""

from __future__ import annotations


class ExponentialBackoff:
    """可复位的指数退避值对象。

    指数封顶（``max_exponent``）防浮点溢出：调用次数无界增长时
    ``factor ** n`` 会在数十次方后超出浮点表示范围抛 OverflowError，
    异常会逃出重连循环并终止采集/恢复任务；把 n 封在 ``max_exponent``
    内，该次方下结果必然已撞上 ``cap_delay_ms``，等待时长不变。
    """

    def __init__(
        self,
        base_delay_ms: int,
        cap_delay_ms: int,
        factor: float = 2.0,
        max_exponent: int = 30,
    ) -> None:
        self._base_delay_ms = base_delay_ms
        self._cap_delay_ms = cap_delay_ms
        self._factor = factor
        self._max_exponent = max_exponent
        self._exponent = 0

    def next_delay_ms(self) -> int:
        """返回当前等待毫秒数并推进到下一次的指数。"""
        delay_ms = min(int(self._base_delay_ms * (self._factor**self._exponent)), self._cap_delay_ms)
        self._exponent = min(self._exponent + 1, self._max_exponent)
        return delay_ms

    def reset(self) -> None:
        """归零指数：下一次 ``next_delay_ms`` 重新返回 ``base_delay_ms``。"""
        self._exponent = 0
