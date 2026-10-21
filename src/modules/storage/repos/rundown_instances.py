"""RundownInstanceRepo —— 流程单实例快照仓储（rundown_instances + rundown_instance_segments）。

一场直播一份实例快照：头行承载游标与计时锚点（崩溃恢复读一行即还原），
环节快照行随变更事务内全量替换（防模板漂移——恢复源是实例，不是模板）。
出入参用 :class:`RundownInstance` Pydantic 模型，本仓储不解析字段语义。
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from src.modules.storage.models.rundown import RundownInstance, RundownInstanceSegment
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms


class RundownInstanceRepo(BaseRepo):
    """rundown_instances / rundown_instance_segments 两张表的读写。"""

    @staticmethod
    def _assemble(header: sqlite3.Row, segment_rows: List[sqlite3.Row]) -> RundownInstance:
        """头行 + 按 seq 有序的环节行 → :class:`RundownInstance`。"""
        return RundownInstance(
            live_session_id=int(header["live_session_id"]),
            template_id=str(header["template_id"]),
            seg_index=int(header["seg_index"]),
            segment_started_at_ms=int(header["segment_started_at_ms"]),
            paused_at_ms=header["paused_at_ms"],
            rundown_started_at_ms=int(header["rundown_started_at_ms"]),
            accumulated_pause_ms=int(header["accumulated_pause_ms"]),
            accumulated_session_pause_ms=int(header["accumulated_session_pause_ms"]),
            updated_at_ms=int(header["updated_at_ms"]),
            segments=[
                RundownInstanceSegment(
                    seq=int(row["seq"]),
                    seg_id=str(row["seg_id"]),
                    title=str(row["title"]),
                    task_description=str(row["task_description"]),
                    expected_ms=int(row["expected_ms"]),
                    min_duration_ms=row["min_duration_ms"],
                    notes=row["notes"],
                )
                for row in segment_rows
            ],
        )

    async def get_by_session(self, live_session_id: int) -> Optional[RundownInstance]:
        """按场次取实例快照（头 + 环节行组装）；未命中返回 ``None``。"""

        def _exec() -> Optional[RundownInstance]:
            with self._manager.transaction() as conn:
                header = conn.execute(
                    "SELECT * FROM rundown_instances WHERE live_session_id=?",
                    (live_session_id,),
                ).fetchone()
                if header is None:
                    return None
                segment_rows = conn.execute(
                    "SELECT seq, seg_id, title, task_description, expected_ms, min_duration_ms, notes"
                    " FROM rundown_instance_segments WHERE live_session_id=? ORDER BY seq ASC",
                    (live_session_id,),
                ).fetchall()
                return self._assemble(header, segment_rows)

        return await self._run_in_executor(_exec)

    async def upsert(self, snapshot: RundownInstance) -> None:
        """写整份实例快照：头 upsert + 环节行事务内全量替换。

        ``updated_at_ms`` 由本层刷新为 ``now_ms()``（快照写入时刻）；调用方
        传入值不落库——快照的新鲜度以落库时刻为准。
        """
        ts = now_ms()

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO rundown_instances ("
                    "live_session_id, template_id, seg_index, segment_started_at_ms, paused_at_ms,"
                    " rundown_started_at_ms, accumulated_pause_ms, accumulated_session_pause_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(live_session_id) DO UPDATE SET"
                    " template_id=excluded.template_id,"
                    " seg_index=excluded.seg_index,"
                    " segment_started_at_ms=excluded.segment_started_at_ms,"
                    " paused_at_ms=excluded.paused_at_ms,"
                    " rundown_started_at_ms=excluded.rundown_started_at_ms,"
                    " accumulated_pause_ms=excluded.accumulated_pause_ms,"
                    " accumulated_session_pause_ms=excluded.accumulated_session_pause_ms,"
                    " updated_at_ms=excluded.updated_at_ms",
                    (
                        snapshot.live_session_id,
                        snapshot.template_id,
                        snapshot.seg_index,
                        snapshot.segment_started_at_ms,
                        snapshot.paused_at_ms,
                        snapshot.rundown_started_at_ms,
                        snapshot.accumulated_pause_ms,
                        snapshot.accumulated_session_pause_ms,
                        ts,
                    ),
                )
                conn.execute(
                    "DELETE FROM rundown_instance_segments WHERE live_session_id=?",
                    (snapshot.live_session_id,),
                )
                conn.executemany(
                    "INSERT INTO rundown_instance_segments ("
                    "live_session_id, seq, seg_id, title, task_description, expected_ms, min_duration_ms, notes"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            snapshot.live_session_id,
                            seg.seq,
                            seg.seg_id,
                            seg.title,
                            seg.task_description,
                            seg.expected_ms,
                            seg.min_duration_ms,
                            seg.notes,
                        )
                        for seg in snapshot.segments
                    ],
                )

        await self._run_in_executor(_exec)

    async def delete_by_session(self, live_session_id: int) -> bool:
        """按场次删除实例快照（头 + 环节行）；头行不存在返回 ``False``。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                conn.execute("DELETE FROM rundown_instance_segments WHERE live_session_id=?", (live_session_id,))
                cur = conn.execute("DELETE FROM rundown_instances WHERE live_session_id=?", (live_session_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)


__all__ = ["RundownInstanceRepo"]
