"""ArchiveRepo —— 场次档案仓储（archive_lines）。

每场正式场下播整理写 ≤8 行，标题加她播出原句的逐字片段，写完不改；运营者可以删单行。
档案是「她记得自己上一场干了什么」的唯一来源：常驻简报取最近一个已整理场次的档案，
回想工具按场次查更早的档案。

不存游戏列——游戏相关的行由标题自己写上游戏名；也不存碎片标记与场次序号，
「上一场」由场次表的时间序决定。
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional, Tuple

from src.modules.storage.models.memory import ArchiveLine
from src.modules.storage.repos._base import BaseRepo
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

# 档案行读面的列（与 ArchiveLine 字段一一对应）
_ARCHIVE_COLUMNS = "id, live_session_id, text, quote, evidence_chat_id"


def delete_archive_lines_by_session(conn: sqlite3.Connection, *, live_session_id: int) -> int:
    """删除某一场的全部档案行，返回删除条数（场次改类型、删除场次时用）。

    模块级函数供跨表事务内的调用方复用同一条删除语句；ArchiveRepo 的异步方法
    只是它的事务包装。
    """
    cur = conn.execute("DELETE FROM archive_lines WHERE live_session_id=?", (int(live_session_id),))
    return int(cur.rowcount or 0)


class ArchiveRepo(BaseRepo):
    """archive_lines 表的读写面。"""

    async def insert_line(
        self,
        *,
        live_session_id: int,
        text: str,
        quote: str,
        evidence_chat_id: int,
    ) -> int:
        """写一行档案，返回主键；标题或原话为空时不写（返回 0）。

        档案行自带原话作证据，两者缺一就失去意义（调用方给了空白串也不入库）；
        内容与原话都去首尾空白后再写。
        """
        title = (text or "").strip()
        quote_text = (quote or "").strip()
        if not title or not quote_text:
            return 0

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id) VALUES (?, ?, ?, ?)",
                    (int(live_session_id), title, quote_text, int(evidence_chat_id)),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_lines(self, *, live_session_id: int) -> List[ArchiveLine]:
        """某一场的档案行（写入顺序：整理时按模型给出的次序写）。"""

        def _exec() -> List[ArchiveLine]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_ARCHIVE_COLUMNS} FROM archive_lines WHERE live_session_id=? ORDER BY id ASC",  # noqa: S608
                    (int(live_session_id),),
                ).fetchall()
                return [ArchiveLine.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def get_last_archived_session_id(self, *, kind: SessionKind = SESSION_KIND_LIVE) -> Optional[int]:
        """最近一个已整理、有档案行且属于该类型的场次主键；一场都没有返回 None。

        「上一场」由此定义：整理失败或当场没写出档案行的场次不算，主播不会把一场
        空场次当成上一场来回忆。
        """

        def _exec() -> Optional[int]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT s.id FROM live_sessions s"
                    " JOIN archive_lines a ON a.live_session_id = s.id"
                    " WHERE s.kind=? GROUP BY s.id ORDER BY s.started_at_ms DESC, s.id DESC LIMIT 1",
                    (kind,),
                ).fetchone()
                return int(row[0]) if row else None

        return await self._run_in_executor(_exec)

    async def list_archived_session_bounds(self, *, limit: int = 200) -> List[Tuple[int, int]]:
        """有档案行的场次与它的开播时刻（开播时刻倒序），供回想工具逐场检索。

        只用档案行判断"这一场有没有档案"，不看 live_chat：保留期清理会把早年弹幕删掉，
        档案还在，回想工具不能因此查不到。
        """

        def _exec() -> List[Tuple[int, int]]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT s.id AS session_id, s.started_at_ms AS started_at_ms FROM live_sessions s"
                    " JOIN archive_lines a ON a.live_session_id = s.id"
                    " GROUP BY s.id ORDER BY s.started_at_ms DESC, s.id DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
                return [(int(row["session_id"]), int(row["started_at_ms"] or 0)) for row in rows]

        return await self._run_in_executor(_exec)

    async def delete_line(self, *, line_id: int) -> bool:
        """运营者删除一行档案；行不存在返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM archive_lines WHERE id=?", (int(line_id),))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_by_session(self, *, live_session_id: int) -> int:
        """删除某一场的全部档案行，返回删除条数（场次改类型、删除场次时用）。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                return delete_archive_lines_by_session(conn, live_session_id=int(live_session_id))

        return await self._run_in_executor(_exec)


__all__ = ["ArchiveRepo", "delete_archive_lines_by_session"]
