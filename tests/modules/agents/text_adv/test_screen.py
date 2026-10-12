"""screen 模块测试：稳定判定与双层去重。

用可注入帧序列的假 capture 覆盖三类行为——
连续两帧相同即稳定返回、帧永不相同则超时降级、识别文本空白差异规范化后判同。
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from src.agents.text_adv.screen import (
    StableFrameResult,
    frame_key,
    text_key,
    wait_stable,
)
from src.modules.vision.look_at_screen import ScreenCaptureResult


class FakeCapture:
    """按预设序列逐次返回帧的假采集后端。

    序列耗尽后，若末项为 ``None`` 则继续返回 ``None``（模拟采集持续失败），
    否则返回形如 ``b"gen-<序号>"`` 的互不相同字节（模拟永不静止的动画）。
    """

    def __init__(self, frames: List[Optional[bytes]]) -> None:
        self._frames = frames
        self._calls = 0

    def capture(
        self,
        monitor_index: int,
        region: Optional[Tuple[int, int, int, int]] = None,
        max_width: Optional[int] = None,
    ) -> ScreenCaptureResult:
        if self._calls < len(self._frames):
            image = self._frames[self._calls]
        elif self._frames[-1] is None:
            image = None
        else:
            image = f"gen-{self._calls}".encode("utf-8")
        self._calls += 1
        return ScreenCaptureResult(image=image, captured_at_ms=int(time.time() * 1000))


async def test_wait_stable_returns_on_two_identical_frames() -> None:
    """连续两帧相同即稳定：返回该帧且未超时。"""
    capture = FakeCapture([b"A", b"A", b"B"])
    result = await wait_stable(capture, region=None, monitor_index=1, sample_ms=5, consecutive=2, timeout_ms=2000)
    assert isinstance(result, StableFrameResult)
    assert result.image == b"A"
    assert result.timed_out is False


async def test_wait_stable_times_out_with_last_frame() -> None:
    """帧永不相同：在超时上限附近返回最后一帧并携带超时标志，不抛异常不死等。"""
    capture = FakeCapture([b"F1", b"F2", b"F3", b"F4", None])
    start = time.monotonic()
    result = await wait_stable(capture, region=None, monitor_index=1, sample_ms=5, consecutive=2, timeout_ms=300)
    elapsed_ms = (time.monotonic() - start) * 1000
    assert result.timed_out is True
    # 最后一帧 = 超时前最后一次成功捕获（其后采集持续失败，不影响已持有帧）
    assert result.image == b"F4"
    # 允许调度余量，但必须远小于死等
    assert elapsed_ms < 1500


async def test_text_key_normalizes_whitespace() -> None:
    """识别文本级指纹：空白差异（数量/换行/首尾）规范化后判同，内容不同判异。"""
    assert text_key("你好  世界\n\t下一行  ") == text_key("你好 世界 下一行")
    assert text_key("第一屏") != text_key("第二屏")
    # 帧级指纹：同字节判同，不同字节判异
    assert frame_key(b"same") == frame_key(b"same")
    assert frame_key(b"a") != frame_key(b"b")
