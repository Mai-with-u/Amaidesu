"""SessionRecapRepo —— 场次回顾仓储（session_recaps）。

每场直播一份收播总结，以 ``live_session_id`` 唯一定位、按开播时刻排序。
消费面：

- 主播 Agent 收播时写入（LLM 总结本场）；启动时补写上一场缺失的回顾
  （进程异常退出时收播钩子没跑到）；
- 开播后按"最近几场"读出注入"上场回顾"段；
- ``memory_recall_session`` 工具按"往前第 n 场"取单场回顾。
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from src.modules.storage.repos._base import BaseRepo
from src.modules.time_utils import now_ms


class SessionRecapRepo(BaseRepo):
    """session_recaps 表的读写面。"""

    async def upsert_recap(
        self,
        *,
        live_session_id: int,
        started_at_ms: int,
        ended_at_ms: int,
        recap_text: str,
    ) -> bool:
        """写入或覆盖一场的回顾；空文本不写，返回 False。"""
        text = (recap_text or "").strip()
        if not text:
            return False
        ts = now_ms()

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO session_recaps(live_session_id, started_at_ms, ended_at_ms, recap_text, created_at_ms)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(live_session_id) DO UPDATE SET"
                    " started_at_ms=excluded.started_at_ms,"
                    " ended_at_ms=excluded.ended_at_ms,"
                    " recap_text=excluded.recap_text,"
                    " created_at_ms=excluded.created_at_ms",
                    (int(live_session_id), int(started_at_ms), int(ended_at_ms), text, ts),
                )
                return True

        return await self._run_in_executor(_exec)

    async def get_recap(self, *, live_session_id: int) -> Optional[sqlite3.Row]:
        """按场次主键取回顾行；没有返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return conn.execute(
                    "SELECT live_session_id, started_at_ms, ended_at_ms, recap_text, created_at_ms"
                    " FROM session_recaps WHERE live_session_id=?",
                    (int(live_session_id),),
                ).fetchone()

        return await self._run_in_executor(_exec)

    async def list_recaps_by_session_ids(self, *, live_session_ids: List[int]) -> List[sqlite3.Row]:
        """按场次主键集合取回顾行（复盘聚合的多场一次读取；顺序不保证）。"""

        def _exec() -> List[sqlite3.Row]:
            placeholders = ",".join("?" for _ in live_session_ids)
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT live_session_id, started_at_ms, ended_at_ms, recap_text, created_at_ms"
                        f" FROM session_recaps WHERE live_session_id IN ({placeholders})",  # noqa: S608 子句为代码内常量
                        [int(sid) for sid in live_session_ids],
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    async def list_recent_recaps(
        self, *, limit: int = 3, exclude_session_id: Optional[int] = None
    ) -> List[sqlite3.Row]:
        """最近几场的回顾（开播时刻倒序，最近一场在前）。

        ``exclude_session_id`` 用来排除正在进行的本场——本场还没收播，
        即使有残留回顾行也不算"上一场"。
        """
        page_limit = max(1, min(int(limit), 20))

        def _exec() -> List[sqlite3.Row]:
            clauses: List[str] = []
            params: List[object] = []
            if exclude_session_id is not None:
                clauses.append("live_session_id<>?")
                params.append(int(exclude_session_id))
            where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT live_session_id, started_at_ms, ended_at_ms, recap_text, created_at_ms"
                        f" FROM session_recaps{where_sql} ORDER BY started_at_ms DESC, id DESC LIMIT ?",  # noqa: S608
                        [*params, page_limit],
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    async def list_ended_sessions_without_recap(self, *, limit: int = 1) -> List[sqlite3.Row]:
        """已结束却没有回顾的场次（结束时刻倒序）——启动补写的待办清单。

        只看已结账的场次（``ended_at_ms`` 非空）；进行中的场次由收播钩子负责。
        """
        page_limit = max(1, min(int(limit), 20))

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT s.id AS live_session_id, s.started_at_ms, s.ended_at_ms, s.title"
                        " FROM live_sessions s"
                        " LEFT JOIN session_recaps r ON r.live_session_id = s.id"
                        " WHERE s.ended_at_ms IS NOT NULL AND r.id IS NULL"
                        " ORDER BY s.ended_at_ms DESC LIMIT ?",
                        (page_limit,),
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    async def delete_recap(self, *, live_session_id: int) -> bool:
        """删除一场的回顾（场次删除级联或人工清理）；行不存在返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM session_recaps WHERE live_session_id=?", (int(live_session_id),))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)


__all__ = ["SessionRecapRepo"]
