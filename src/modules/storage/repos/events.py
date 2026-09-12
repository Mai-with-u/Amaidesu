"""EventRepo —— 事件记录仓储（event_history + game_events）。

- ``event_history``：语义域事件流（EventHistoryService 落库），payload 存
  完整载荷 JSON（回放端按 event_name 取回后直接反序列化）；按日查询与
  按事件名过滤都是热路径，时间与 (事件名, 时间) 建索引。
- ``game_events``：游戏里程碑/安全阀/异常（StorageLedger 从 ``game.*``
  事件落库）。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from src.modules.storage.repos._base import BaseRepo


class EventRepo(BaseRepo):
    """event_history / game_events 两张事件记录表的读写。"""

    async def insert_event(
        self,
        *,
        record_id: str,
        event_name: str,
        timestamp_ms: int,
        level: str = "info",
        source: str = "",
        summary: str = "",
        payload_json: str = "{}",
    ) -> int:
        """插入一条事件历史行，返回 lastrowid。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO event_history ("
                    "record_id, event_name, timestamp_ms, level, source, summary, payload"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (record_id, event_name, timestamp_ms, level, source, summary, payload_json),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_event_dates(self, event_name: str) -> List[str]:
        """列出指定事件名有记录的本地日期（``YYYY-MM-DD``，时间正序）。"""
        rows = await self._execute(
            "SELECT DISTINCT date(timestamp_ms / 1000, 'unixepoch', 'localtime') AS d"
            " FROM event_history WHERE event_name = ? ORDER BY d",
            (event_name,),
        )
        return [str(row["d"]) for row in rows if row["d"] is not None]

    async def get_day_events(self, date_str: str, *, event_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """读取指定本地日期的事件历史行（时间正序）；``event_name`` 为 None 时取全部事件。

        返回 dict 行（record_id / event_name / timestamp_ms / level / source / summary / payload），
        payload 为原始 JSON 字符串，由调用方解析。
        """
        if event_name is not None:
            sql = (
                "SELECT record_id, event_name, timestamp_ms, level, source, summary, payload"
                " FROM event_history WHERE event_name = ?"
                " AND date(timestamp_ms / 1000, 'unixepoch', 'localtime') = ?"
                " ORDER BY timestamp_ms, seq"
            )
            params: Any = (event_name, date_str)
        else:
            sql = (
                "SELECT record_id, event_name, timestamp_ms, level, source, summary, payload"
                " FROM event_history"
                " WHERE date(timestamp_ms / 1000, 'unixepoch', 'localtime') = ?"
                " ORDER BY timestamp_ms, seq"
            )
            params = (date_str,)
        rows = await self._execute(sql, params)
        return [dict(row) for row in rows]

    async def insert_game_event(
        self,
        *,
        live_session_id: int,
        game: str,
        event_type: str,
        message: str,
        scene: Optional[str] = None,
        timestamp_ms: int = 0,
    ) -> int:
        """插入一条 game_events 行，返回 lastrowid。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO game_events (live_session_id, game, event_type, message, scene, timestamp_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (live_session_id, game, event_type, message, scene, timestamp_ms),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def _execute(self, sql: str, params: Any = ()) -> List[sqlite3.Row]:
        """仓储内单条 SQL 执行（SELECT 为主），返回 Row 列表。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cursor = conn.execute(sql, params if params is not None else ())
                return list(cursor.fetchall())

        return await self._run_in_executor(_exec)


__all__ = ["EventRepo"]
