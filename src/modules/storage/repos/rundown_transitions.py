"""RundownTransitionRepo —— 流程单变更事实仓储（rundown_transitions，append-only）。

每次环节变更一行事实，只插不改不删（分析"实际停留 vs 预期停留"的数据面）；
``(live_session_id, at_ms)`` 索引支撑按场次的时间窗读取。
"""

from __future__ import annotations

from typing import List, Optional

from src.modules.storage.models.rundown import RundownTransition
from src.modules.storage.repos._base import BaseRepo
from src.modules.time_utils import now_ms


class RundownTransitionRepo(BaseRepo):
    """rundown_transitions 表的写面与按场读取。"""

    async def insert(self, transition: RundownTransition) -> None:
        """追加一条变更事实行；``updated_at_ms`` 由本层刷新为 ``now_ms()``。"""
        ts = now_ms()

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO rundown_transitions ("
                    "live_session_id, rundown_id, action, seg_id, seg_title, seg_index,"
                    " by, at_ms, expected_ms, task_description, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        transition.live_session_id,
                        transition.rundown_id,
                        transition.action,
                        transition.seg_id,
                        transition.seg_title,
                        transition.seg_index,
                        transition.by,
                        transition.at_ms,
                        transition.expected_ms,
                        transition.task_description,
                        ts,
                    ),
                )

        await self._run_in_executor(_exec)

    async def list_by_session(self, live_session_id: int, *, limit: Optional[int] = None) -> List[RundownTransition]:
        """按场次取事实行（``at_ms`` 升序）；``limit`` 限条数时取最近的那些（返回仍升序）。"""

        def _exec() -> List[RundownTransition]:
            with self._manager.transaction() as conn:
                sql = (
                    "SELECT live_session_id, rundown_id, action, seg_id, seg_title, seg_index,"
                    " by, at_ms, expected_ms, task_description, updated_at_ms"
                    " FROM rundown_transitions WHERE live_session_id=? ORDER BY at_ms ASC, id ASC"
                )
                params: list = [live_session_id]
                if limit is not None:
                    # DESC LIMIT 取最近 N 条后反转回升序（排序不变量见 repos/chat.py 模块说明）
                    rows = conn.execute(
                        sql.replace("ORDER BY at_ms ASC, id ASC", "ORDER BY at_ms DESC, id DESC LIMIT ?"),  # noqa: S608 子句为代码内常量
                        [*params, limit],
                    ).fetchall()
                    rows = list(reversed(rows))
                else:
                    rows = conn.execute(sql, params).fetchall()
                return [
                    RundownTransition(
                        live_session_id=int(row["live_session_id"]),
                        rundown_id=str(row["rundown_id"]),
                        action=str(row["action"]),
                        seg_id=str(row["seg_id"]),
                        seg_title=str(row["seg_title"]),
                        seg_index=int(row["seg_index"]),
                        by=str(row["by"]),
                        at_ms=int(row["at_ms"]),
                        expected_ms=row["expected_ms"],
                        task_description=row["task_description"],
                        updated_at_ms=int(row["updated_at_ms"]),
                    )
                    for row in rows
                ]

        return await self._run_in_executor(_exec)


__all__ = ["RundownTransitionRepo"]
