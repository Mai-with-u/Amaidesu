"""SessionRepo —— live_sessions 场次表仓储。

场次行由 LiveSessionManager 创建与结账：一行 = 一场直播（有开始/结束边界），
主键 AUTOINCREMENT；房间/频道是普通属性列。本仓储只提供行级领域方法，
不持有"当前场次"状态。
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from src.modules.storage.repos._base import BaseRepo


class SessionRepo(BaseRepo):
    """live_sessions 场次表的读写。"""

    async def insert_live_session(
        self,
        *,
        stream_id: str = "",
        platform: str = "unknown",
        started_at_ms: int,
        title: Optional[str] = None,
        source: str = "manual",
    ) -> int:
        """插入一场新场次（``ended_at_ms`` 为 NULL 即进行中），返回场次主键。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO live_sessions ("
                    "stream_id, platform, started_at_ms, title, source, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (stream_id, platform, started_at_ms, title, source, started_at_ms),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def close_live_session(self, *, live_session_id: int, ended_at_ms: int) -> bool:
        """写入场次结束时间（幂等：已结束的场次不覆盖）。返回是否命中行。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET ended_at_ms=?, updated_at_ms=? WHERE id=? AND ended_at_ms IS NULL",
                    (ended_at_ms, ended_at_ms, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def update_live_session_stats(
        self,
        *,
        live_session_id: int,
        heat: int,
        viewer_count: int,
        audience_total: int,
        updated_at_ms: int,
    ) -> bool:
        """更新场次实时状态（热度/计数心跳）。行不存在（如默认场次被清理）返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE live_sessions SET heat=?, viewer_count=?, audience_total=?, updated_at_ms=? WHERE id=?",
                    (heat, viewer_count, audience_total, updated_at_ms, live_session_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def get_live_session(self, *, live_session_id: int) -> Optional[sqlite3.Row]:
        """按场次主键查单行；未命中返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return conn.execute("SELECT * FROM live_sessions WHERE id=?", (live_session_id,)).fetchone()

        return await self._run_in_executor(_exec)

    async def list_dangling_live_sessions(self) -> List[sqlite3.Row]:
        """列出未结账的显式场次（ended_at_ms IS NULL）。

        用于启动期收口：上次进程未正常退出的残留"进行中"场次。
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(conn.execute("SELECT * FROM live_sessions WHERE ended_at_ms IS NULL").fetchall())

        return await self._run_in_executor(_exec)

    async def delete_live_session(self, *, live_session_id: int) -> bool:
        """删除场次行并级联清除其明细数据。

        级联范围：live_chat / gifts / super_chats / topics / game_events /
        timeline_summary（均以 ``live_session_id`` 引用场次主键）。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                for table in ("live_chat", "gifts", "super_chats", "topics", "game_events", "timeline_summary"):
                    conn.execute(f"DELETE FROM {table} WHERE live_session_id=?", (live_session_id,))  # noqa: S608 表名为代码内常量
                cur = conn.execute("DELETE FROM live_sessions WHERE id=?", (live_session_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def list_live_sessions(
        self,
        *,
        limit: int = 50,
        source: Optional[str] = None,
        title_keyword: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        """列出场次（附消息数），供场次列表/回看选择。

        排序：按开始时间倒序。无显式场次期间不创建兜底行；列表只含历史
        显式场次。
        筛选：``source`` 精确匹配来源；``title_keyword`` 对 title 做包含匹配。

        Args:
            limit: 最多返回条数
            source: 来源过滤（manual / replay / legacy）；None 不过滤
            title_keyword: 标题关键字；None 或空串不过滤
        """

        conditions: List[str] = []
        params: List[object] = []
        if source:
            conditions.append("s.source=?")
            params.append(source)
        if title_keyword:
            conditions.append("s.title LIKE ?")
            params.append(f"%{title_keyword}%")
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT s.*, ("
                        "SELECT COUNT(*) FROM live_chat c WHERE c.live_session_id = s.id"
                        ") AS message_count "
                        f"FROM live_sessions s {where_clause} "
                        "ORDER BY s.started_at_ms DESC LIMIT ?",
                        params,
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)


__all__ = ["SessionRepo"]
