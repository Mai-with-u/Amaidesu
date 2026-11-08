"""ClipRepo —— 切片段（clip_segments）的读写。

AI 选段写一批提议（先清同场旧 AI 段再插），运营在 WebUI 增删改段并发起切割，
切割器回填状态与产物路径。场次被删时段级联删除。
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from src.modules.logging import get_logger
from src.modules.storage.models.clipping import ClipSegment, ClipSegmentUpdate, NewClipSegment
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

logger = get_logger("ClipRepo")

_COLUMNS = (
    "id, live_session_id, origin, start_ms, end_ms, title, reason, status,"
    " output_path, error_message, created_at_ms, updated_at_ms"
)


def _segment_of(row: object) -> ClipSegment:
    return ClipSegment.model_validate(dict(row))  # type: ignore[arg-type]


class ClipRepo(BaseRepo):
    """切片段仓储：AI 提议整批换、运营手调单行改、切割回填状态。"""

    async def replace_ai_segments(self, *, live_session_id: int, segments: Sequence[NewClipSegment]) -> int:
        """用一批新 AI 提议替换该场现有的全部 AI 段（运营手建段不受影响），返回写入行数。"""
        now = now_ms()
        rows = [
            (
                int(segment.live_session_id),
                segment.origin,
                int(segment.start_ms),
                int(segment.end_ms),
                segment.title,
                segment.reason,
                segment.status,
                now,
                now,
            )
            for segment in segments
        ]

        def _exec() -> int:
            with self._manager.transaction() as conn:
                conn.execute(
                    "DELETE FROM clip_segments WHERE live_session_id=? AND origin='ai'", (int(live_session_id),)
                )
                conn.executemany(
                    "INSERT INTO clip_segments(live_session_id, origin, start_ms, end_ms, title, reason,"
                    " status, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            return len(rows)

        return await self._run_in_executor(_exec)

    async def add_segment(self, *, segment: NewClipSegment) -> ClipSegment:
        """运营手建一段，返回落库后的完整行。"""

        def _exec() -> ClipSegment:
            now = now_ms()
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO clip_segments(live_session_id, origin, start_ms, end_ms, title, reason,"
                    " status, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(segment.live_session_id),
                        segment.origin,
                        int(segment.start_ms),
                        int(segment.end_ms),
                        segment.title,
                        segment.reason,
                        segment.status,
                        now,
                        now,
                    ),
                )
                row_id = int(cur.lastrowid or 0)
                row = conn.execute(f"SELECT {_COLUMNS} FROM clip_segments WHERE id=?", (row_id,)).fetchone()  # noqa: S608 常量列名
                return _segment_of(row)

        return await self._run_in_executor(_exec)

    async def update_segment(self, *, segment_id: int, update: ClipSegmentUpdate) -> Optional[ClipSegment]:
        """按运营手调更新一行（None 字段不动），返回更新后的行；行不存在返回 None。"""
        assignments: List[str] = []
        params: List[object] = []
        if update.start_ms is not None:
            assignments.append("start_ms=?")
            params.append(int(update.start_ms))
        if update.end_ms is not None:
            assignments.append("end_ms=?")
            params.append(int(update.end_ms))
        if update.title is not None:
            assignments.append("title=?")
            params.append(update.title)
        if update.status is not None:
            assignments.append("status=?")
            params.append(update.status)
        if not assignments:
            return await self.get_segment(segment_id=segment_id)

        def _exec() -> Optional[ClipSegment]:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    f"UPDATE clip_segments SET {', '.join(assignments)}, updated_at_ms=? WHERE id=?",  # noqa: S608 常量列名白名单拼装
                    (*params, now_ms(), int(segment_id)),
                )
                if int(cur.rowcount or 0) == 0:
                    return None
                row = conn.execute(f"SELECT {_COLUMNS} FROM clip_segments WHERE id=?", (int(segment_id),)).fetchone()  # noqa: S608 常量列名
                return _segment_of(row)

        return await self._run_in_executor(_exec)

    async def delete_segment(self, *, segment_id: int) -> bool:
        """删一行，返回是否真的删了。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM clip_segments WHERE id=?", (int(segment_id),))
                return int(cur.rowcount or 0) > 0

        return await self._run_in_executor(_exec)

    async def set_status(
        self,
        *,
        segment_id: int,
        status: str,
        output_path: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """切割器回填状态（done 带 output_path，failed 带 error_message），返回是否命中。"""
        assignments = ["status=?", "updated_at_ms=?"]
        params: List[object] = [status, now_ms()]
        if output_path is not None:
            assignments.append("output_path=?")
            params.append(output_path)
        if error_message is not None:
            assignments.append("error_message=?")
            params.append(error_message)

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    f"UPDATE clip_segments SET {', '.join(assignments)} WHERE id=?",  # noqa: S608 常量列名白名单拼装
                    (*params, int(segment_id)),
                )
                return int(cur.rowcount or 0) > 0

        return await self._run_in_executor(_exec)

    async def get_segment(self, *, segment_id: int) -> Optional[ClipSegment]:
        """按主键取一行；不存在返回 None。"""

        def _exec() -> Optional[ClipSegment]:
            with self._manager.transaction() as conn:
                row = conn.execute(f"SELECT {_COLUMNS} FROM clip_segments WHERE id=?", (int(segment_id),)).fetchone()  # noqa: S608 常量列名
                return _segment_of(row) if row is not None else None

        return await self._run_in_executor(_exec)

    async def list_session_segments(self, *, live_session_id: int) -> List[ClipSegment]:
        """某一场的全部切片段（按起点先后）。"""

        def _exec() -> List[ClipSegment]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM clip_segments WHERE live_session_id=?"  # noqa: S608 常量列名
                    " ORDER BY start_ms ASC, id ASC",
                    (int(live_session_id),),
                ).fetchall()
            return [_segment_of(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def list_segments_by_ids(self, *, segment_ids: Sequence[int]) -> List[ClipSegment]:
        """按主键集合取行（按起点先后）；不存在的 id 静默跳过。"""
        ids = sorted({int(item) for item in segment_ids})
        if not ids:
            return []

        def _exec() -> List[ClipSegment]:
            with self._manager.transaction() as conn:
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM clip_segments WHERE id IN ({placeholders})"  # noqa: S608 常量列名占位符
                    " ORDER BY start_ms ASC, id ASC",
                    ids,
                ).fetchall()
            return [_segment_of(row) for row in rows]

        return await self._run_in_executor(_exec)


__all__ = ["ClipRepo"]
