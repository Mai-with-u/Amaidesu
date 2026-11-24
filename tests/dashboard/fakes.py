"""dashboard API 测试的假对象（全库只此一份，别在测试文件里现写）。"""

from __future__ import annotations

from typing import Any


class FakeClipSelector:
    """AI 选段替身：返回预设提议或抛预设错误，记录调用（ClippingService(selector=) 接缝用）。

    提议对象只需带 start_ms/end_ms/title/reason 属性（service 落库时逐个取值）。
    """

    def __init__(self, *, suggestions: list[Any] | None = None, error: Exception | None = None) -> None:
        self.suggestions = suggestions if suggestions is not None else []
        self.error = error
        self.calls: list[int] = []

    async def select(self, *, live_session_id: int) -> list[Any]:
        self.calls.append(live_session_id)
        if self.error is not None:
            raise self.error
        return self.suggestions
