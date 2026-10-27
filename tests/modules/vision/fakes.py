"""测试用视觉后端替身：注入预置结果序列，驱动 LookAtScreenProvider 的成功/异常/超时路径"""

from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional, Tuple

from src.modules.vision.capture_types import ScreenCaptureResult


class FakeScreenCapture:
    """测试用 ScreenCapture，可注入预置的截图结果序列。

    与生产 ``MssScreenCapture`` 的协议同形：
    ``capture(monitor_index, region=None, max_width=None)``。

    Example:
        >>> cap = FakeScreenCapture()
        >>> cap.queue_png(b"\\x89PNG...fake bytes...", width=1920, height=1080)
        >>> provider = LookAtScreenProvider(screen_capture=cap)
    """

    def __init__(self) -> None:
        self._queue: List[ScreenCaptureResult] = []
        self.calls: List[dict[str, Any]] = []
        self._raise: Optional[BaseException] = None

    def queue(self, result: ScreenCaptureResult) -> None:
        """入队一个采集结果（下次 capture 调用返回）。"""
        self._queue.append(result)

    def queue_png(self, image_bytes: bytes, *, width: int = 1920, height: int = 1080) -> None:
        """便捷方法：入队一个 PNG 图像。"""
        self.queue(
            ScreenCaptureResult(
                image=image_bytes,
                width=width,
                height=height,
                mime_type="image/png",
                captured_at_ms=int(time.time() * 1000),
                monitor_index=1,
            )
        )

    def queue_empty(self) -> None:
        """便捷方法：入队一个空 result（image=None），代表无显示/无权限。"""
        self.queue(ScreenCaptureResult(captured_at_ms=int(time.time() * 1000)))

    def set_raise(self, exc: Optional[BaseException]) -> None:
        """下一次 capture 调用抛该异常（用于测试 capture 异常降级）。"""
        self._raise = exc

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        self.calls.append(
            {
                "monitor_index": int(monitor_index),
                "region": region,
                "max_width": max_width,
            }
        )
        if self._raise is not None:
            exc = self._raise
            self._raise = None
            raise exc
        if self._queue:
            return self._queue.pop(0)
        # 缺省：返回空 result（代表无显示/无图像）
        return ScreenCaptureResult(captured_at_ms=int(time.time() * 1000), monitor_index=int(monitor_index))


class FakeTextReader:
    """测试用 TextReader，可注入预置的文本结果序列（异步契约）。"""

    def __init__(self) -> None:
        self._queue: List[str] = []
        self.calls: List[dict[str, Any]] = []
        self._raise: Optional[BaseException] = None
        self._hang_until: Optional[float] = None  # 运行中事件循环的 monotonic 截止时刻

    def queue_text(self, text: str) -> None:
        self._queue.append(text)

    def set_raise(self, exc: Optional[BaseException]) -> None:
        """下一次 read 调用抛该异常（用于测试 reader 异常降级）。"""
        self._raise = exc

    def set_hang_until_ms(self, deadline_ms_from_now: int) -> None:
        """挂起到指定时间点后返回空串（用于测试 reader 超时降级）。"""
        loop_now = asyncio.get_running_loop().time()
        self._hang_until = loop_now + deadline_ms_from_now / 1000.0

    async def read(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/png",
        question: Optional[str] = None,
    ) -> str:
        self.calls.append(
            {
                "image_bytes_len": len(image_bytes),
                "mime_type": mime_type,
                "question": question,
            }
        )
        if self._raise is not None:
            exc = self._raise
            self._raise = None
            raise exc
        if self._hang_until is not None:
            now = asyncio.get_running_loop().time()
            remaining = self._hang_until - now
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._hang_until = None
            return ""
        if self._queue:
            return self._queue.pop(0)
        return ""
