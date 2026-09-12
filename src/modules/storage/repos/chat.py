"""ChatRepo —— 直播明细三表仓储（live_chat + gifts + super_chats）。

三表均带 simulated 贯穿列，详见 schema.py "命名硬规则"。本仓储承接
RoomMessagePayload → 表的写入入口与常见读路径；表结构权威在 schema.py，
本层不复制。

排序不变量：所有时间序列查询以 ``timestamp_ms`` 排序，禁止依赖 INSERT 顺序。
取"最近 N 条"必须先 DESC LIMIT，再 Python 内反转；直接 ASC LIMIT 会取到
最老的一批，破坏时间线回看的正确性。
"""

from __future__ import annotations

import sqlite3
from typing import Any, List, Optional

from src.modules.storage.repos._base import BaseRepo


class ChatRepo(BaseRepo):
    """live_chat / gifts / super_chats 三张明细表的读写。"""

    async def insert_live_chat(
        self,
        *,
        live_session_id: int,
        timestamp_ms: int,
        sender_role: str,
        content: str,
        message_type: str,
        sender_id: Optional[str] = None,
        sender_name: Optional[str] = None,
        message_id: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        tool_result: Optional[str] = None,
        simulated: bool = False,
    ) -> int:
        """插入一条 live_chat 行，返回 lastrowid。

        ``message_id``（观众行）/ ``reply_to_message_id``（主播行）构成
        "主播发言回复了哪条弹幕"的关联键（互动分析数据面）。
        """

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO live_chat ("
                    "live_session_id, timestamp_ms, sender_role, sender_id, sender_name,"
                    " content, message_type, message_id, reply_to_message_id, tool_result, simulated"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        live_session_id,
                        timestamp_ms,
                        sender_role,
                        sender_id,
                        sender_name,
                        content,
                        message_type,
                        message_id,
                        reply_to_message_id,
                        tool_result,
                        1 if simulated else 0,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def insert_gift(
        self,
        *,
        live_session_id: int,
        timestamp_ms: int,
        user_id: str,
        user_name: str,
        gift_name: str,
        gift_count: int,
        simulated: bool = False,
    ) -> int:
        """插入一条 gifts 行，返回 lastrowid。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO gifts ("
                    "live_session_id, timestamp_ms, user_id, user_name,"
                    " gift_name, gift_count, simulated"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        live_session_id,
                        timestamp_ms,
                        user_id,
                        user_name,
                        gift_name,
                        gift_count,
                        1 if simulated else 0,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def insert_super_chat(
        self,
        *,
        live_session_id: int,
        timestamp_ms: int,
        user_id: str,
        user_name: str,
        amount: float,
        message: str,
        simulated: bool = False,
    ) -> int:
        """插入一条 super_chats 行，返回 lastrowid。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO super_chats ("
                    "live_session_id, timestamp_ms, user_id, user_name,"
                    " amount, message, simulated"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        live_session_id,
                        timestamp_ms,
                        user_id,
                        user_name,
                        amount,
                        message,
                        1 if simulated else 0,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_recent_live_chat(
        self,
        *,
        live_session_id: int,
        limit: int = 30,
        before_timestamp_ms: Optional[int] = None,
        sender_role: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        """取指定场次最近的 ``limit`` 条消息，按时间**正序**返回（旧→新）。

        实现：先 ``ORDER BY timestamp_ms DESC LIMIT ?`` 拿最新窗口，再 Python 内
        ``list(reversed(...))`` 反转。``before_timestamp_ms`` 用于分页/窗口截断
        （仅取 < 该时间的消息）。``sender_role`` 可选过滤发送方角色
        （``"viewer"``=观众 / ``"assistant"``=主播）。
        """

        def _exec() -> List[sqlite3.Row]:
            clauses = ["live_session_id=?"]
            params: List[Any] = [live_session_id]
            if sender_role is not None:
                clauses.append("sender_role=?")
                params.append(sender_role)
            if before_timestamp_ms is not None:
                clauses.append("timestamp_ms<?")
                params.append(before_timestamp_ms)
            params.append(limit)
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "SELECT * FROM live_chat WHERE " + " AND ".join(clauses) + " ORDER BY timestamp_ms DESC LIMIT ?",
                    params,
                )
                rows = cur.fetchall()
            # DESC 取到的是 [新→旧]，反转回 [旧→新] 满足调用方约定
            return list(reversed(rows))

        return await self._run_in_executor(_exec)

    async def list_messages_by_user(
        self,
        *,
        user_id: str,
        since_timestamp_ms: int,
        limit: int = 50,
    ) -> List[sqlite3.Row]:
        """按用户过滤其发言历史（时间正序）。

        ``sender_id`` 只匹配 viewer 行；assistant 行的 sender_id 是主播名，
        不会混入该查询。
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "SELECT * FROM live_chat WHERE sender_id=? AND timestamp_ms >= ? ORDER BY timestamp_ms ASC LIMIT ?",
                    (user_id, since_timestamp_ms, limit),
                )
                return list(cur.fetchall())

        return await self._run_in_executor(_exec)

    async def count_session_details(self, *, live_session_id: int) -> int:
        """统计场次明细行数（live_chat + gifts + super_chats），供空场次判定。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT ("
                    "(SELECT COUNT(*) FROM live_chat WHERE live_session_id=?) + "
                    "(SELECT COUNT(*) FROM gifts WHERE live_session_id=?) + "
                    "(SELECT COUNT(*) FROM super_chats WHERE live_session_id=?)"
                    ") AS n",
                    (live_session_id, live_session_id, live_session_id),
                ).fetchone()
                return int(row["n"]) if row else 0

        return await self._run_in_executor(_exec)

    async def list_session_gifts(self, *, live_session_id: int, limit: int = 300) -> List[sqlite3.Row]:
        """取指定场次的礼物明细行（时间正序），供回看时间线合并。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT * FROM gifts WHERE live_session_id=? ORDER BY timestamp_ms ASC LIMIT ?",
                        (live_session_id, limit),
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    async def list_session_super_chats(self, *, live_session_id: int, limit: int = 300) -> List[sqlite3.Row]:
        """取指定场次的 SC 明细行（时间正序），供回看时间线合并。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT * FROM super_chats WHERE live_session_id=? ORDER BY timestamp_ms ASC LIMIT ?",
                        (live_session_id, limit),
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)


__all__ = ["ChatRepo"]
