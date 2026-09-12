"""RundownRepo —— rundowns 流程单表仓储。

整场流程单整体读写（segments_json 一次性存整段列表，不建子表）。
序列化与反序列化由 ``Rundown`` / ``RundownSegment`` 数据模型承担，仓储
不解析 JSON 内部形状。``created_at_ms`` / ``updated_at_ms`` 由本层维护。
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, List, Optional

from src.modules.storage.repos._base import BaseRepo
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.agents.streamer.rundown.rundown import Rundown


class RundownRepo(BaseRepo):
    """rundowns 流程单表的读写。"""

    @staticmethod
    def _serialize_rundown_segments(rundown: Rundown) -> str:
        """将 ``rundown.segments`` 列表序列化为 JSON 字符串（``rundown_id`` / ``title`` 走独立列）。"""
        return json.dumps([seg.model_dump() for seg in rundown.segments], ensure_ascii=False)

    @staticmethod
    def _deserialize_rundown(row: sqlite3.Row) -> Rundown:
        """从 rundowns 行重建 :class:`Rundown`（逐段 ``RundownSegment.model_validate``）。"""
        # 函数体内 import 限 5 种情形之一——此处属"循环 import 规避"：
        # 仓储顶层导入 rundown 会触发 src.agents.streamer/__init__.py 装配链
        # （StreamerAgent → Planner → memory → 本仓储）的循环。
        from src.agents.streamer.rundown.rundown import Rundown, RundownSegment  # noqa: PLC0415

        segments_data = json.loads(str(row["segments_json"]))
        segments = [RundownSegment.model_validate(item) for item in segments_data]
        return Rundown(
            rundown_id=str(row["id"]),
            title=str(row["title"]),
            segments=segments,
        )

    async def upsert_rundown(self, rundown: Rundown) -> None:
        """按 ``id`` 主键插入或更新流程单；``created_at_ms`` 仅首次插入写入、``updated_at_ms`` 每次刷新为 ``now_ms()``。"""
        ts = now_ms()
        segments_json = self._serialize_rundown_segments(rundown)

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO rundowns (id, title, segments_json, created_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET"
                    " title=excluded.title,"
                    " segments_json=excluded.segments_json,"
                    " updated_at_ms=excluded.updated_at_ms",
                    (rundown.rundown_id, rundown.title, segments_json, ts, ts),
                )

        await self._run_in_executor(_exec)

    async def get_rundown(self, rundown_id: str) -> Optional[Rundown]:
        """按 ``id`` 取单条流程单；未命中返回 ``None``。"""

        def _exec() -> Optional[Rundown]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT id, title, segments_json, created_at_ms, updated_at_ms FROM rundowns WHERE id=?",
                    (rundown_id,),
                ).fetchone()
                if row is None:
                    return None
                return self._deserialize_rundown(row)

        return await self._run_in_executor(_exec)

    async def list_rundowns(self) -> List[Rundown]:
        """列出全部流程单，按 ``created_at_ms`` 升序。"""

        def _exec() -> List[Rundown]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT id, title, segments_json, created_at_ms, updated_at_ms"
                    " FROM rundowns ORDER BY created_at_ms ASC, id ASC"
                ).fetchall()
                return [self._deserialize_rundown(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def delete_rundown(self, rundown_id: str) -> bool:
        """按 ``id`` 删除流程单；行不存在返回 ``False``。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM rundowns WHERE id=?", (rundown_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)


__all__ = ["RundownRepo"]
