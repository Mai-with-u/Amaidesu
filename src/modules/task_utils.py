"""后台任务持有工具：fire-and-forget 协程的强引用与异常可见化。

asyncio 对任务只持弱引用——裸 ``create_task`` 的返回值无人接时任务可能被
GC 中途回收，且异常无人观察（静默失败）。所有"发后不管"的后台协程一律
经 :func:`spawn_background_task` 创建。
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, Optional

from src.modules.logging import ModuleLogger

__all__ = ["spawn_background_task"]


def spawn_background_task(
    coro: Coroutine[Any, Any, Any],
    *,
    logger: ModuleLogger,
    tasks: set,
    label: str,
) -> Optional[asyncio.Task]:
    """创建后台任务并纳入 ``tasks`` 强引用持有，返回任务（创建失败返回 None）。

    行为对齐 ``EventBus._background_tasks`` 正典模式：

    - 无事件循环（RuntimeError）→ warning 后放弃，不抛
    - 任务完成自动从 ``tasks`` 移除
    - 非取消导致的未捕获异常 → error 日志（异常已吞，不外传）

    ``tasks`` 由调用方提供（通常为实例状态，``__init__`` 里初始化空 set），
    需要停止时汇合的场景由调用方自行 ``gather``。
    """
    try:
        task = asyncio.create_task(coro, name=label)
    except RuntimeError as exc:
        logger.warning(f"{label} 任务创建失败（无事件循环，已放弃）: {exc}")
        return None

    tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(f"{label} 后台任务未捕获异常: {type(exc).__name__}: {exc}")

    task.add_done_callback(_on_done)
    return task
