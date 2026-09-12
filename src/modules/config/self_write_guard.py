"""自写抑制标记：配置管线写盘前登记，FileWatcher 消费时据此跳过重入。

写入完成到 watcher 事件送达之间存在异步窗口；标记带存活期（TTL），
超时自动失效——watcher 侧消费丢失时不会永久抑制重载。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

# 标记存活期：覆盖"写盘 → watcher 轮询到事件"的最大正常延迟
_MARK_TTL_S = 10.0

_lock = threading.Lock()
_marks: dict[str, float] = {}  # 规范化路径（小写盘符）→ 失效时刻（monotonic 秒）


def _normalize(path: Path | str) -> str:
    return str(Path(path).resolve()).lower()


def mark_self_write(path: Path | str, ttl_s: float = _MARK_TTL_S) -> None:
    """登记一次自写：path 在 ttl 内产生的变更事件应被 watcher 忽略。"""
    key = _normalize(path)
    with _lock:
        _marks[key] = time.monotonic() + ttl_s


def consume_self_write(path: Path | str) -> bool:
    """查询并清除标记。命中且未过期返回 True（调用方跳过该事件）。"""
    key = _normalize(path)
    now = time.monotonic()
    with _lock:
        deadline = _marks.pop(key, None)
    return deadline is not None and deadline >= now


__all__ = ["mark_self_write", "consume_self_write"]
