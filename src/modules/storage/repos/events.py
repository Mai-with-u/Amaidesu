"""EventRepo —— 游戏事件记录仓储（game_events）。

``game_events``：游戏里程碑/安全阀/异常（StorageLedger 从 ``game.*``
事件落库）。事件流的持久观察由业务表承担（消息流落 live_chat 等），
本仓储不再承接事件历史副本。
"""

from __future__ import annotations

from typing import List, Optional

from src.modules.storage.models.events import GameEventRecord, GameEventTypeCount
from src.modules.storage.repos._base import BaseRepo


class EventRepo(BaseRepo):
    """game_events 表的写入与按场读取。"""

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

    async def list_session_game_events(self, *, live_session_id: int, limit: int = 60) -> List[GameEventRecord]:
        """取指定场次的游戏事件（时间正序，最多 ``limit`` 条最近的）——场次回顾的游戏进展原料。"""
        page_limit = max(1, min(int(limit), 500))

        def _exec() -> List[GameEventRecord]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT game, event_type, message, scene, timestamp_ms FROM game_events"
                    " WHERE live_session_id=? ORDER BY timestamp_ms DESC LIMIT ?",
                    (int(live_session_id), page_limit),
                ).fetchall()
                return [GameEventRecord.model_validate(dict(row)) for row in reversed(rows)]

        return await self._run_in_executor(_exec)

    async def count_game_events_by_type(self, *, live_session_ids: List[int]) -> List[GameEventTypeCount]:
        """按类型计数场次游戏事件（多场 IN 合并，复盘页游戏侧指标数据源）。"""

        def _exec() -> List[GameEventTypeCount]:
            placeholders = ",".join("?" for _ in live_session_ids)
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT event_type, COUNT(*) AS n FROM game_events"
                    f" WHERE live_session_id IN ({placeholders}) GROUP BY event_type",  # noqa: S608 子句为代码内常量
                    [int(sid) for sid in live_session_ids],
                ).fetchall()
                return [GameEventTypeCount.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)


__all__ = ["EventRepo"]
