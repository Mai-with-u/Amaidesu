"""音频模块测试假对象。

全库对 ``AudioSink`` 协议的测试桩只此一份：各测试文件从这里导入。
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np


class RecordingSink:
    """记录 start/feed/stop 调用的 ``AudioSink`` 桩；``boom=True`` 时全链路抛异常。"""

    def __init__(self, boom: bool = False) -> None:
        self.calls: List[Tuple[str, object]] = []
        self.boom = boom

    def start(self, utterance_id: str = "") -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("start", utterance_id))

    def feed(self, chunk: np.ndarray, sample_rate: int) -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("feed", (chunk, sample_rate)))

    def stop(self) -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("stop", None))

    def fed_chunks(self) -> List[np.ndarray]:
        """按序返回已递入的音频块。"""
        return [payload[0] for name, payload in self.calls if name == "feed"]
