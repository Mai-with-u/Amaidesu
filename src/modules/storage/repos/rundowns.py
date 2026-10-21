"""RundownRepo —— 流程单模板仓储（rundowns 元信息 + rundown_segments 环节行）。

模板拆行持久化：``rundowns`` 一行元信息，环节落 ``rundown_segments`` 行表
（``seq`` 定序，凡环节皆行）。``Rundown`` / ``RundownSegment`` 数据模型
承担形状契约，本仓储只做行集组装；对外方法签名不随存储形态变化。
``created_at_ms`` / ``updated_at_ms`` 由本层维护；环节行在写路径事务内
"先删后插"全量替换（模板编辑语义即整单覆写）。
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from src.modules.storage.models.rundown import Rundown, RundownSegment
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms


class RundownRepo(BaseRepo):
    """rundowns + rundown_segments 两张表的读写。"""

    @staticmethod
    def _segment_row_params(rundown_id: str, seq: int, seg: RundownSegment) -> tuple:
        """组装单条环节行插入参数（``seq`` 即模板内顺序）。"""
        return (
            rundown_id,
            seq,
            seg.id,
            seg.title,
            seg.task_description,
            seg.expected_ms,
            seg.min_duration_ms,
            seg.notes,
        )

    @classmethod
    def _assemble_rundown(cls, header: sqlite3.Row, segment_rows: List[sqlite3.Row]) -> Rundown:
        """元信息行 + 按 seq 有序的环节行 → :class:`Rundown`。"""
        segments = [
            RundownSegment(
                id=str(row["seg_id"]),
                title=str(row["title"]),
                task_description=str(row["task_description"]),
                expected_ms=int(row["expected_ms"]),
                min_duration_ms=row["min_duration_ms"],
                notes=row["notes"],
            )
            for row in segment_rows
        ]
        return Rundown(rundown_id=str(header["id"]), title=str(header["title"]), segments=segments)

    @staticmethod
    def _load_segment_rows(conn: sqlite3.Connection, rundown_ids: List[str]) -> Dict[str, List[sqlite3.Row]]:
        """一次取多个流程单的环节行，按 ``(rundown_id, seq)`` 分组（组内 seq 升序）。"""
        placeholders = ",".join("?" for _ in rundown_ids)
        rows = conn.execute(
            f"SELECT rundown_id, seq, seg_id, title, task_description, expected_ms, min_duration_ms, notes"  # noqa: S608 子句为代码内常量
            f" FROM rundown_segments WHERE rundown_id IN ({placeholders}) ORDER BY rundown_id ASC, seq ASC",
            rundown_ids,
        ).fetchall()
        grouped: Dict[str, List[sqlite3.Row]] = {rid: [] for rid in rundown_ids}
        for row in rows:
            grouped[str(row["rundown_id"])].append(row)
        return grouped

    async def upsert_rundown(self, rundown: Rundown) -> None:
        """按 ``id`` 主键插入或更新流程单；``created_at_ms`` 仅首次插入写入、``updated_at_ms`` 每次刷新为 ``now_ms()``。

        环节行在同一事务内全量替换（DELETE 旧行 + INSERT 新行），模板编辑
        语义即整单覆写。
        """
        ts = now_ms()

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO rundowns (id, title, created_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET"
                    " title=excluded.title,"
                    " updated_at_ms=excluded.updated_at_ms",
                    (rundown.rundown_id, rundown.title, ts, ts),
                )
                conn.execute("DELETE FROM rundown_segments WHERE rundown_id=?", (rundown.rundown_id,))
                conn.executemany(
                    "INSERT INTO rundown_segments ("
                    "rundown_id, seq, seg_id, title, task_description, expected_ms, min_duration_ms, notes"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        self._segment_row_params(rundown.rundown_id, seq, seg)
                        for seq, seg in enumerate(rundown.segments)
                    ],
                )

        await self._run_in_executor(_exec)

    async def get_rundown(self, rundown_id: str) -> Optional[Rundown]:
        """按 ``id`` 取单条流程单；未命中返回 ``None``。"""

        def _exec() -> Optional[Rundown]:
            with self._manager.transaction() as conn:
                header = conn.execute("SELECT id, title FROM rundowns WHERE id=?", (rundown_id,)).fetchone()
                if header is None:
                    return None
                segment_rows = conn.execute(
                    "SELECT seq, seg_id, title, task_description, expected_ms, min_duration_ms, notes"
                    " FROM rundown_segments WHERE rundown_id=? ORDER BY seq ASC",
                    (rundown_id,),
                ).fetchall()
                return self._assemble_rundown(header, segment_rows)

        return await self._run_in_executor(_exec)

    async def list_rundowns(self) -> List[Rundown]:
        """列出全部流程单，按 ``created_at_ms`` 升序（环节行一次取齐，组内 seq 升序）。"""

        def _exec() -> List[Rundown]:
            with self._manager.transaction() as conn:
                headers = conn.execute("SELECT id, title FROM rundowns ORDER BY created_at_ms ASC, id ASC").fetchall()
                if not headers:
                    return []
                grouped = self._load_segment_rows(conn, [str(row["id"]) for row in headers])
                return [self._assemble_rundown(header, grouped[str(header["id"])]) for header in headers]

        return await self._run_in_executor(_exec)

    async def delete_rundown(self, rundown_id: str) -> bool:
        """按 ``id`` 删除流程单（含环节行）；行不存在返回 ``False``。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                conn.execute("DELETE FROM rundown_segments WHERE rundown_id=?", (rundown_id,))
                cur = conn.execute("DELETE FROM rundowns WHERE id=?", (rundown_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)


__all__ = ["RundownRepo"]
