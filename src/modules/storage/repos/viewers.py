"""ViewerRepo —— viewers 观众统计表仓储。

跨场客观数字（发言/送礼/被回复/互动计数），由 StorageLedger 在主表落库
同点 upsert 写穿，dashboard 读取排行。
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from src.modules.storage.repos._base import BaseRepo


class ViewerRepo(BaseRepo):
    """viewers 观众统计表的读写。"""

    # list_viewer_stats 排序白名单——防 SQL 注入，禁止字符串拼接列名
    _ORDER_BY_WHITELIST: tuple = (
        "message_count",
        "gift_count",
        "replied_count",
        "interaction_count",
        "last_active_ms",
    )

    async def get_viewer_stats(self, *, user_id: str) -> Optional[sqlite3.Row]:
        """按 user_id 查单行观众统计；未命中返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cur = conn.execute("SELECT * FROM viewers WHERE user_id=?", (user_id,))
                return cur.fetchone()

        return await self._run_in_executor(_exec)

    async def list_viewer_stats(
        self,
        *,
        limit: int = 100,
        order_by: str = "message_count",
    ) -> List[sqlite3.Row]:
        """列出观众统计排行。

        ``order_by`` 仅接受白名单列名（``message_count``/``gift_count``/
        ``replied_count``/``interaction_count``/``last_active_ms``），
        非法值直接 ``ValueError``——避免任何列名拼接。
        """
        if order_by not in self._ORDER_BY_WHITELIST:
            raise ValueError(f"list_viewer_stats order_by 非法: {order_by!r}，允许值: {self._ORDER_BY_WHITELIST}")

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    f"SELECT * FROM viewers ORDER BY {order_by} DESC LIMIT ?",  # noqa: S608
                    (limit,),
                )
                return list(cur.fetchall())

        return await self._run_in_executor(_exec)

    async def upsert_viewer_message(self, *, user_id: str, user_name: str, timestamp_ms: int) -> None:
        """观众发言计数：``message_count`` +1，``interaction_count`` +1。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, last_active_ms"
                    ") VALUES (?, ?, 1, 0, 0, 1, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "user_name=excluded.user_name, "
                    "message_count=message_count+1, "
                    "interaction_count=interaction_count+1, "
                    "last_active_ms=excluded.last_active_ms",
                    (user_id, user_name, timestamp_ms),
                )

        await self._run_in_executor(_exec)

    async def upsert_viewer_gift(self, *, user_id: str, user_name: str, timestamp_ms: int) -> None:
        """观众送礼计数：``gift_count`` +1，``interaction_count`` +1。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, last_active_ms"
                    ") VALUES (?, ?, 0, 1, 0, 1, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "user_name=excluded.user_name, "
                    "gift_count=gift_count+1, "
                    "interaction_count=interaction_count+1, "
                    "last_active_ms=excluded.last_active_ms",
                    (user_id, user_name, timestamp_ms),
                )

        await self._run_in_executor(_exec)

    async def upsert_viewer_replied(self, *, user_id: str, timestamp_ms: int) -> None:
        """主播回复该用户计数：``replied_count`` +1，``interaction_count`` +1。

        若该 user_id 首次进入统计行（未发过言也未送过礼），以 "?" 占位 user_name
        首建——不强制外部补传用户名（用户名的权威来源是发言/礼物事件）。
        """

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewers("
                    "user_id, user_name, message_count, gift_count, replied_count,"
                    " interaction_count, last_active_ms"
                    ") VALUES (?, '?', 0, 0, 1, 1, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "replied_count=replied_count+1, "
                    "interaction_count=interaction_count+1, "
                    "last_active_ms=excluded.last_active_ms",
                    (user_id, timestamp_ms),
                )

        await self._run_in_executor(_exec)


__all__ = ["ViewerRepo"]
