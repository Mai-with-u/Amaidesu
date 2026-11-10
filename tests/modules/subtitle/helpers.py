"""字幕模块测试假对象。"""

from __future__ import annotations

from typing import Callable, List, Optional


class FakeScheduler:
    """段切换兜底定时器替身：记录调度请求，测试显式触发，时间不真实流逝。"""

    def __init__(self) -> None:
        self._entries: List[dict] = []

    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> Callable[[], None]:
        entry = {"delay_ms": delay_ms, "callback": callback, "cancelled": False, "fired": False}
        self._entries.append(entry)

        def cancel() -> None:
            entry["cancelled"] = True

        return cancel

    def pending(self) -> List[dict]:
        """当前未取消、未触发的定时请求。"""
        return [entry for entry in self._entries if not entry["cancelled"] and not entry["fired"]]

    def fire_next(self) -> Optional[dict]:
        """按调度顺序触发下一个未取消的定时；无定时可触发时返回 None。"""
        for entry in self._entries:
            if not entry["cancelled"] and not entry["fired"]:
                entry["fired"] = True
                entry["callback"]()
                return entry
        return None

    def delays(self) -> List[int]:
        """全部调度请求的延时毫秒表（含已取消/已触发的，按调度序）。"""
        return [entry["delay_ms"] for entry in self._entries]
