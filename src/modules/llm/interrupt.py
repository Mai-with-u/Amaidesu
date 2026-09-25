"""等待 LLM 完整生成，同时让主动中断可靠地回收底层请求。"""

import asyncio
from typing import Awaitable, Callable, Optional, TypeVar

from src.modules.llm.errors import LLMInterruptedError
from src.modules.logging import get_logger

_logger = get_logger(__name__)
_T = TypeVar("_T")


async def _reap(task: asyncio.Future[_T]) -> None:
    """调用方停止生成后，先等待连接清理完成，再结束上层任务。"""
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # 主动取消是请求的正常终态，底层 finally 已完成连接回收。
        return
    except Exception as exc:
        _logger.debug(f"LLM 请求取消后的清理异常: {exc}", exc=True)


async def guarded_call(
    factory: Callable[[], Awaitable[_T]],
    *,
    interrupt_flag: Optional[asyncio.Event] = None,
) -> _T:
    """持续等待生成；用户中断或父任务取消时，收回本次调用及其子任务。"""
    if interrupt_flag is not None and interrupt_flag.is_set():
        raise LLMInterruptedError("调用方已中断，请求未启动")
    task = asyncio.ensure_future(factory())
    interrupt_waiter = asyncio.create_task(interrupt_flag.wait()) if interrupt_flag is not None else None
    try:
        if interrupt_waiter is None:
            return await task
        # 任一方完成即可返回，正常生成结束不再等待中断信号。
        done, _pending = await asyncio.wait({task, interrupt_waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        await _reap(task)
        raise LLMInterruptedError("调用方中断，LLM 请求已取消")
    except asyncio.CancelledError:
        await _reap(task)
        raise
    finally:
        if interrupt_waiter is not None:
            await _reap(interrupt_waiter)
