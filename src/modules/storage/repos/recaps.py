"""SessionRecapRepo —— 场次回顾仓储（session_recaps）。

每场直播一份收播总结，以 ``live_session_id`` 唯一定位、按开播时刻排序。
消费面：

- 主播 Agent 收播时写入（LLM 总结本场）；启动时补完上一场没整理完的场次
  （进程异常退出时收播钩子没跑到，或停止 Agent 时整理被取消）；
- 回顾行顺带记收播整理进度：长期记忆合并过没有、三步是否都走完；
- 开播后按"最近几场"读出注入"上场回顾"段；
- ``memory_recall_session`` 工具按"往前第 n 场"取单场回顾。

两处读取都按场次类型过滤：正式场与彩排场各自一条回顾线。
"""

from __future__ import annotations

from typing import List, Optional

from src.modules.storage.models.memory import SessionRecap, UnsettledSession
from src.modules.storage.repos._base import BaseRepo
from src.modules.types.session_kind import SessionKind
from src.modules.utils.time_utils import now_ms

# 回顾行读面的列（与 SessionRecap 字段一一对应）
_RECAP_COLUMNS = (
    "live_session_id, started_at_ms, ended_at_ms, recap_text, created_at_ms, long_memory_merged_at_ms, settled_at_ms"
)


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
        """写入或覆盖一场的回顾；空文本不写，返回 False。

        覆盖写不动整理进度两列：进度记的是这一场整理到了哪一步，与回顾正文改没改无关。
        """
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

    async def get_recap(self, *, live_session_id: int) -> Optional[SessionRecap]:
        """按场次主键取回顾行；没有返回 None。"""

        def _exec() -> Optional[SessionRecap]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    f"SELECT {_RECAP_COLUMNS} FROM session_recaps WHERE live_session_id=?",  # noqa: S608 列名为代码内常量
                    (int(live_session_id),),
                ).fetchone()
                return SessionRecap.model_validate(dict(row)) if row else None

        return await self._run_in_executor(_exec)

    async def list_recaps_by_session_ids(self, *, live_session_ids: List[int]) -> List[SessionRecap]:
        """按场次主键集合取回顾行（复盘聚合的多场一次读取；顺序不保证）。"""

        def _exec() -> List[SessionRecap]:
            placeholders = ",".join("?" for _ in live_session_ids)
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_RECAP_COLUMNS} FROM session_recaps WHERE live_session_id IN ({placeholders})",  # noqa: S608 子句为代码内常量
                    [int(sid) for sid in live_session_ids],
                ).fetchall()
                return [SessionRecap.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def list_recent_recaps(
        self,
        *,
        limit: int = 3,
        exclude_session_id: Optional[int] = None,
        kind: Optional[SessionKind] = None,
    ) -> List[SessionRecap]:
        """最近几场的回顾（开播时刻倒序，最近一场在前）。

        ``exclude_session_id`` 用来排除正在进行的本场——本场还没收播，
        即使有残留回顾行也不算"上一场"。``kind`` 只取该类型场次的回顾：
        正式场只接正式场的回顾，彩排场只接彩排场的，两条记忆线互不串；
        类型以场次行为准（人手更正后立即生效），None 不过滤。
        """
        page_limit = max(1, min(int(limit), 20))

        def _exec() -> List[SessionRecap]:
            clauses: List[str] = []
            params: List[object] = []
            if exclude_session_id is not None:
                clauses.append("live_session_id<>?")
                params.append(int(exclude_session_id))
            if kind is not None:
                clauses.append("live_session_id IN (SELECT id FROM live_sessions WHERE kind=?)")
                params.append(kind)
            where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_RECAP_COLUMNS} FROM session_recaps{where_sql}"
                    " ORDER BY started_at_ms DESC, id DESC LIMIT ?",  # noqa: S608
                    [*params, page_limit],
                ).fetchall()
                return [SessionRecap.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def list_unsettled_sessions(self, *, limit: int = 1) -> List[UnsettledSession]:
        """已结束却没整理完的场次（结束时刻倒序）——启动补写的待办清单。

        没整理完 = 没有回顾（收播钩子没跑到、回顾没写成），或有回顾但整理完成时刻为空
        （回顾写完后整理被打断）。只看已结账的场次（``ended_at_ms`` 非空）；进行中的场次
        由收播钩子负责。
        """
        page_limit = max(1, min(int(limit), 20))

        def _exec() -> List[UnsettledSession]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT s.id AS live_session_id, s.started_at_ms, s.ended_at_ms, s.title"
                    " FROM live_sessions s"
                    " LEFT JOIN session_recaps r ON r.live_session_id = s.id"
                    " WHERE s.ended_at_ms IS NOT NULL AND (r.id IS NULL OR r.settled_at_ms IS NULL)"
                    " ORDER BY s.ended_at_ms DESC LIMIT ?",
                    (page_limit,),
                ).fetchall()
                return [UnsettledSession.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def mark_long_memory_merged(self, *, live_session_id: int) -> bool:
        """记下这一场已合并进长期记忆；没有回顾行时不记，返回 False。"""
        return await self._stamp_progress("long_memory_merged_at_ms", int(live_session_id))

    async def mark_settled(self, *, live_session_id: int) -> bool:
        """记下这一场的收播整理三步都走完了；没有回顾行时不记，返回 False。

        回顾没写成的场次记不上完成，下次启动的补写会再试一次回顾。
        """
        return await self._stamp_progress("settled_at_ms", int(live_session_id))

    async def _stamp_progress(self, column: str, live_session_id: int) -> bool:
        """把整理进度列写成当下时刻；``column`` 只取本类内的进度列名常量。"""
        ts = now_ms()

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    f"UPDATE session_recaps SET {column}=? WHERE live_session_id=?",  # noqa: S608 列名为代码内常量
                    (ts, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_recap(self, *, live_session_id: int) -> bool:
        """删除一场的回顾（场次删除级联或人工清理）；行不存在返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM session_recaps WHERE live_session_id=?", (int(live_session_id),))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)


__all__ = ["SessionRecapRepo"]
