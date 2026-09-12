"""域仓储共用基类：连接管理器持有 + to_thread 防漏执行。"""

from __future__ import annotations

import asyncio
from functools import partial

from src.modules.storage.connection import SQLiteConnectionManager


class BaseRepo:
    """域仓储基类。

    仓储是按表域切分的具体类（不做接口/Protocol 抽象）；本基类只共享两件事：
    持有 ``SQLiteConnectionManager``（构造注入，各仓储共享同一连接面与
    WAL/SAVEPOINT 语义），以及统一 ``asyncio.to_thread`` 防漏——调用方
    不需手动 to_thread。
    """

    def __init__(self, manager: SQLiteConnectionManager) -> None:
        self._manager = manager

    async def _run_in_executor(self, fn, /, *args, **kwargs):
        """统一 ``asyncio.to_thread`` 防漏（仓储内所有同步调用都走这里）。"""
        if asyncio.iscoroutinefunction(fn):
            # 不应该到这里（避免失误）；直接 await
            return await fn(*args, **kwargs)
        # functools.partial 处理：kwargs 关键字
        if kwargs:
            return await asyncio.to_thread(partial(fn, *args, **kwargs))
        if args:
            return await asyncio.to_thread(fn, *args)
        return await asyncio.to_thread(fn)


__all__ = ["BaseRepo"]
