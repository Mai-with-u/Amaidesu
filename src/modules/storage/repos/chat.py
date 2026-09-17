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
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional, Tuple

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

    async def list_danmaku_by_date(
        self,
        date_str: str,
        *,
        simulated_only: bool = False,
    ) -> List[sqlite3.Row]:
        """取指定本地日期的全部弹幕行（``message_type='danmaku'``，时间正序）。

        回放引擎的数据源：业务表 ``live_chat`` 是消息流的单一事实源，录制
        回放不再依赖事件历史副本。``simulated_only=True`` 时仅返回录制时已
        标记 simulated 的行。
        """

        def _exec() -> List[sqlite3.Row]:
            clauses = [
                "message_type='danmaku'",
                "date(timestamp_ms / 1000, 'unixepoch', 'localtime') = ?",
            ]
            params: List[Any] = [date_str]
            if simulated_only:
                clauses.append("simulated=1")
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "SELECT * FROM live_chat WHERE " + " AND ".join(clauses) + " ORDER BY timestamp_ms ASC",
                    params,
                )
                return list(cur.fetchall())

        return await self._run_in_executor(_exec)

    async def list_chat_dates(self) -> List[str]:
        """列出 live_chat 有弹幕记录的本地日期（``YYYY-MM-DD``，时间正序）。

        回放日期选择器的数据源：从业务表取 DISTINCT 日期，无场次数据则
        返回空列表。
        """

        def _exec() -> List[str]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT date(timestamp_ms / 1000, 'unixepoch', 'localtime') AS d"
                    " FROM live_chat WHERE message_type='danmaku' ORDER BY d"
                ).fetchall()
            return [str(row["d"]) for row in rows if row["d"] is not None]

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

    async def list_user_dialogue(
        self,
        *,
        user_id: str,
        before_timestamp_ms: Optional[int] = None,
        limit: int = 30,
    ) -> List[sqlite3.Row]:
        """按观众取"对话批次"：其消息 + 主播对这些消息的回复，时间正序交织。

        分页以观众消息为主轴：先取该观众 ``limit`` 条消息（``before_timestamp_ms``
        为游标，DESC LIMIT 后反转），再按本批 ``message_id`` 反查
        ``sender_role='assistant' AND reply_to_message_id IN (...)`` 的回复行，
        两者按 ``(timestamp_ms, id)`` 合并正序返回。回复行时间必不早于其
        对应消息，因此交织序列单调。游标翻页时回复行不会跨批重复——每批
        只反查本批消息的关联回复。
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                clauses = ["sender_id=?", "sender_role='viewer'"]
                params: List[Any] = [user_id]
                if before_timestamp_ms is not None:
                    clauses.append("timestamp_ms<?")
                    params.append(before_timestamp_ms)
                params.append(limit)
                cur = conn.execute(
                    "SELECT * FROM live_chat WHERE " + " AND ".join(clauses) + " ORDER BY timestamp_ms DESC LIMIT ?",
                    params,
                )
                viewer_rows = list(reversed(cur.fetchall()))
                message_ids = [row["message_id"] for row in viewer_rows if row["message_id"]]
                if not message_ids:
                    return viewer_rows
                placeholders = ",".join("?" * len(message_ids))
                reply_rows = conn.execute(
                    f"SELECT * FROM live_chat WHERE sender_role='assistant'"  # noqa: S608
                    f" AND reply_to_message_id IN ({placeholders}) ORDER BY timestamp_ms ASC",
                    message_ids,
                ).fetchall()
                merged = [*viewer_rows, *reply_rows]
                merged.sort(key=lambda row: (row["timestamp_ms"], row["id"]))
                return merged

        return await self._run_in_executor(_exec)

    async def get_user_activity_bounds(self, *, user_id: str) -> Optional[Tuple[int, int]]:
        """观众在明细三表中的时间边界 ``(first_ms, last_ms)``；无任何明细返回 None。

        覆盖 live_chat（发言）/ gifts / super_chats 三源取最小与最大——只送过
        礼没发过言的观众同样有"首次出现"可考。
        """

        def _exec() -> Optional[Tuple[int, int]]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT MIN(m) AS first_ms, MAX(m) AS last_ms FROM ("
                    " SELECT timestamp_ms AS m FROM live_chat WHERE sender_id=? AND sender_role='viewer'"
                    " UNION ALL SELECT timestamp_ms FROM gifts WHERE user_id=?"
                    " UNION ALL SELECT timestamp_ms FROM super_chats WHERE user_id=?"
                    ")",
                    (user_id, user_id, user_id),
                ).fetchone()
                if row is None or row["first_ms"] is None:
                    return None
                return int(row["first_ms"]), int(row["last_ms"])

        return await self._run_in_executor(_exec)

    async def list_user_gifts(self, *, user_id: str, limit: int = 100) -> List[sqlite3.Row]:
        """取观众的礼物明细行（时间倒序，最新在前）。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT * FROM gifts WHERE user_id=? ORDER BY timestamp_ms DESC LIMIT ?",
                        (user_id, limit),
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    async def list_user_super_chats(self, *, user_id: str, limit: int = 100) -> List[sqlite3.Row]:
        """取观众的 SC 明细行（时间倒序，最新在前）。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT * FROM super_chats WHERE user_id=? ORDER BY timestamp_ms DESC LIMIT ?",
                        (user_id, limit),
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    async def summarize_user_contributions(self, *, user_id: str) -> Dict[str, float]:
        """观众贡献汇总：礼物总件数、SC 总额（元）与 SC 条数。"""

        def _exec() -> Dict[str, float]:
            with self._manager.transaction() as conn:
                gift_row = conn.execute(
                    "SELECT COALESCE(SUM(gift_count), 0) AS n FROM gifts WHERE user_id=?",
                    (user_id,),
                ).fetchone()
                sc_row = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0.0) AS amount, COUNT(*) AS n FROM super_chats WHERE user_id=?",
                    (user_id,),
                ).fetchone()
                return {
                    "gift_total_count": int(gift_row["n"]) if gift_row else 0,
                    "sc_total_amount": float(sc_row["amount"]) if sc_row else 0.0,
                    "sc_total_count": int(sc_row["n"]) if sc_row else 0,
                }

        return await self._run_in_executor(_exec)

    async def list_user_sessions(self, *, user_id: str) -> List[sqlite3.Row]:
        """观众参与过的场次：按场次聚合发言数与时间范围，最近参与在前。

        join ``live_sessions`` 取标题；场次行可能已删除（明细级联删除保证
        不会，标题缺省仍以 NULL 容忍历史数据）。
        """

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT lc.live_session_id, ls.title, COUNT(*) AS message_count,"
                        " MIN(lc.timestamp_ms) AS first_ms, MAX(lc.timestamp_ms) AS last_ms"
                        " FROM live_chat lc LEFT JOIN live_sessions ls ON ls.id=lc.live_session_id"
                        " WHERE lc.sender_id=? AND lc.sender_role='viewer'"
                        " GROUP BY lc.live_session_id"
                        " ORDER BY first_ms DESC",
                        (user_id,),
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)

    async def daily_danmaku_counts(self, *, days: int = 30) -> List[sqlite3.Row]:
        """按本地日期聚合观众弹幕量（互动分析页折线数据），日期正序。

        只看 ``message_type='danmaku'``；``days`` 天前的本地零点起算。
        """

        start_dt = datetime.now() - timedelta(days=days)
        start_ms = int(datetime.combine(start_dt.date(), dt_time.min).timestamp() * 1000)

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(
                    conn.execute(
                        "SELECT date(timestamp_ms / 1000, 'unixepoch', 'localtime') AS day,"
                        " COUNT(*) AS count"
                        " FROM live_chat WHERE message_type='danmaku' AND timestamp_ms>=?"
                        " GROUP BY day ORDER BY day",
                        (start_ms,),
                    ).fetchall()
                )

        return await self._run_in_executor(_exec)


__all__ = ["ChatRepo"]
