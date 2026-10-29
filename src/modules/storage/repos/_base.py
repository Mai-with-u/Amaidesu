"""域仓储共用基类：连接管理器持有 + to_thread 防漏执行。"""

from __future__ import annotations

from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.run_in_executor import run_in_executor
from typing import Any, Callable


class BaseRepo:
    """域仓储基类。

    仓储是按表域切分的具体类（不做接口/Protocol 抽象）；本基类只共享两件事：
    持有 ``SQLiteConnectionManager``（构造注入，各仓储共享同一连接面与
    WAL/SAVEPOINT 语义），以及统一 ``asyncio.to_thread`` 防漏——调用方
    不需手动 to_thread。
    """

    def __init__(self, manager: SQLiteConnectionManager) -> None:
        self._manager = manager

    async def _run_in_executor(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """统一 ``asyncio.to_thread`` 防漏（仓储内所有同步调用都走这里）。"""
        return await run_in_executor(fn, *args, **kwargs)

    async def close(self) -> None:
        """关闭共享连接管理器持有的全部连接（幂等，落盘 WAL/事务）。

        各域仓储共享同一管理器，任一仓储关闭一次即可。关闭时仍在事务中的
        后台写入会照常做完再由其线程关连接；关闭后才发起的读写会被拒绝，
        所以调用方仍应先停掉产生读写的后台任务，免得写入丢失。
        """
        await self._run_in_executor(self._manager.close_all)


__all__ = ["BaseRepo"]
