"""统一线程池执行入口：存储层所有真同步调用都经这里进 ``asyncio.to_thread``。

仓储基类与 ``SQLiteDatabase`` 各自持有一份逐字相同的防漏逻辑，收编为本模块
的单一函数（防漏原则：调用方不需手动 to_thread，也不会漏）。
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any, Callable


async def run_in_executor(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """在线程池里执行同步调用；误传协程函数时直接 await（不应发生，防御性兜底）。"""
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    if kwargs:
        return await asyncio.to_thread(partial(fn, *args, **kwargs))
    if args:
        return await asyncio.to_thread(fn, *args)
    return await asyncio.to_thread(fn)


__all__ = ["run_in_executor"]
