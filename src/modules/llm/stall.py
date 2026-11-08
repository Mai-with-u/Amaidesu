"""流式停滞判据

现象：上游连接还在、流却不再产出内容（挂死）。只看总时长上限（用途档的放弃等待
时长）分不清"慢"与"死"——正常的长思考也会持续几十秒，把上限压到那个量级就会误杀
正常的调用。

判据：连续一段时间内没有任何输出块即判定上游挂死，按失败交回上层的模型切换
循环。输出块指模型产出的内容——正文、思考、工具调用参数碎片任一算有输出；只带
结束标记或用量的块不算。

它与总时长上限并存，两个上限各管一件事：总时长管"这次调用整体太久"，停滞管
"上游已经不产出了"。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from src.modules.llm.errors import LLMTimeoutError

__all__ = ["DEFAULT_STALL_TIMEOUT_MS", "StreamStallGuard"]

# 停滞判据的默认上限：连续这么久没有任何输出块即判定上游挂死
DEFAULT_STALL_TIMEOUT_MS = 30_000

# 等待某个 awaitable 并给出超时上限（秒）的协程函数：超时抛 asyncio.TimeoutError
Waiter = Callable[[Awaitable[Any], float], Awaitable[Any]]


def _now_ms() -> int:
    """默认时钟：单调时钟的毫秒值。"""
    return int(time.monotonic() * 1000)


async def _wait_with_timeout(awaitable: Awaitable[Any], timeout_s: float) -> Any:
    """默认等待实现：超过上限就取消等待并抛超时。"""
    return await asyncio.wait_for(awaitable, timeout_s)


class StreamStallGuard:
    """流式停滞守卫：跟踪最后一次收到输出的时刻。

    Args:
        timeout_ms: 停滞上限（毫秒）；连续这么久没有任何输出块即判定挂死
        clock: 单调时钟（毫秒）；测试注入假时钟，推进时间不靠真实等待
        wait: 等待某个 awaitable 并给出超时上限（秒）的协程函数；测试注入替身，
            避免真实等待
    """

    def __init__(
        self,
        timeout_ms: int = DEFAULT_STALL_TIMEOUT_MS,
        *,
        clock: Callable[[], int] = _now_ms,
        wait: Waiter = _wait_with_timeout,
    ) -> None:
        self._timeout_ms = timeout_ms
        self._clock = clock
        self._wait = wait
        self._last_output_at = clock()

    @property
    def timeout_ms(self) -> int:
        """停滞上限（毫秒）。"""
        return self._timeout_ms

    def note_output(self) -> None:
        """记一次有效输出（正文 / 思考 / 工具调用参数碎片）。"""
        self._last_output_at = self._clock()

    def idle_ms(self) -> int:
        """距离上一次输出已经过去多久（毫秒）。"""
        return max(0, self._clock() - self._last_output_at)

    def remaining_ms(self) -> int:
        """距离判定挂死还剩多少毫秒。"""
        return max(0, self._timeout_ms - self.idle_ms())

    async def wait_for(self, awaitable: Awaitable[Any]) -> Any:
        """在停滞上限内等待某个 awaitable；超限即判定上游挂死。

        抛 LLMTimeoutError（上层按失败切下一个模型）。
        """
        remaining_ms = self.remaining_ms()
        if remaining_ms <= 0:
            raise self._stall_error()
        try:
            return await self._wait(awaitable, remaining_ms / 1000)
        except asyncio.TimeoutError as e:
            raise self._stall_error() from e

    def _stall_error(self) -> LLMTimeoutError:
        return LLMTimeoutError(f"流式响应停滞：连续 {self.idle_ms()}ms 没有任何输出（上限 {self._timeout_ms}ms）")
