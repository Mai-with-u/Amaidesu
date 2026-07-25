"""Helpers for interruptible and bounded LLM requests."""

import asyncio
from typing import TypeVar


_T = TypeVar("_T")


async def await_with_timeout_and_interrupt(
    task: asyncio.Task[_T],
    *,
    timeout: float,
    interrupt_flag: asyncio.Event | None,
    poll_interval: float = 0.02,
) -> _T:
    """等待 LLM 请求完成并支持超时/外部中断，确保 httpx 连接得到清理。

    hard timeout prevents a request from hanging indefinitely.  When an interrupt
    event is set, the underlying task is cancelled and awaited so its httpx
    response/connection cleanup can finish before ``CancelledError`` propagates.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            if interrupt_flag is not None and interrupt_flag.is_set():
                _ = task.cancel()
                raise asyncio.CancelledError

            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError

            wait_time = remaining
            if interrupt_flag is not None:
                wait_time = min(wait_time, poll_interval)

            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=wait_time)
            except asyncio.TimeoutError:
                if interrupt_flag is None:
                    _ = task.cancel()
                    raise
    finally:
        if not task.done():
            _ = task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
