"""TopicRepo —— 话题与摘要快照仓储（topics + timeline_summary）。

- ``timeline_summary``：一行一段摘要历史（BackgroundMaintainer 摘要成功后写入）
- ``topics``：当前话题快照投影——每场先清旧行再插最新状态，消费者读到的
  永远是当前话题状态（历史轨迹由 timeline_summary 承担）
"""

from __future__ import annotations

from typing import List

from src.modules.storage.models.topics import TimelineSummaryEntry
from src.modules.storage.repos._base import BaseRepo


class TopicRepo(BaseRepo):
    """topics / timeline_summary 两张快照表的写面，外加 timeline_summary 的按场读取。"""

    async def insert_timeline_summary(
        self,
        *,
        live_session_id: int,
        start_ms: int,
        end_ms: int,
        summary: str,
    ) -> int:
        """插入一条摘要历史行，返回 lastrowid。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO timeline_summary (live_session_id, start_ms, end_ms, summary) VALUES (?, ?, ?, ?)",
                    (live_session_id, start_ms, end_ms, summary),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def delete_session_topics(self, *, live_session_id: int) -> int:
        """清空指定场次的话题快照行（重投影前的第一步），返回删除行数。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM topics WHERE live_session_id=?", (live_session_id,))
                return int(cur.rowcount or 0)

        return await self._run_in_executor(_exec)

    async def insert_topic(
        self,
        *,
        live_session_id: int,
        label: str,
        source: str,
        score: float,
        trend: float,
        duration_ms: int,
        count: int = 0,
    ) -> int:
        """插入一条话题快照行，返回 lastrowid。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO topics (live_session_id, label, source, score, trend, duration_ms, count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (live_session_id, label, source, score, trend, duration_ms, count),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_session_summaries(self, *, live_session_id: int, limit: int = 120) -> List[TimelineSummaryEntry]:
        """取指定场次的话题摘要轨迹（时间正序，最多 ``limit`` 条最近的）——场次回顾的话题原料。"""
        page_limit = max(1, min(int(limit), 1000))

        def _exec() -> List[TimelineSummaryEntry]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT start_ms, end_ms, summary FROM timeline_summary"
                    " WHERE live_session_id=? ORDER BY end_ms DESC, id DESC LIMIT ?",
                    (int(live_session_id), page_limit),
                ).fetchall()
                return [TimelineSummaryEntry.model_validate(dict(row)) for row in reversed(rows)]

        return await self._run_in_executor(_exec)


__all__ = ["TopicRepo"]
