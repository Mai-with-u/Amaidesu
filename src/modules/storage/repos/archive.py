"""ArchiveRepo —— 场次档案仓储（archive_lines）。

每场正式场下播整理写一份滚动档案：本场新事加上一份档案里仍然成立的行，标题加她播出原句的
逐字片段，写完不改；运营者可以删单行。档案是「她记得前几场干了什么」的唯一来源：常驻简报取
最近一个已整理场次的档案，回想工具按场次查更早的档案。

接过来的行记着出自哪一场（``from_session_id``），同一件事因此会出现在好几场的档案里：删场次、
改彩排时连同这些副本一起删（:func:`delete_archive_lines_from_session`）；重新整理某一场只替换
这一场自己的档案（:func:`delete_archive_lines_by_session`），不动它此前接进后面几场的副本。
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional, Tuple

from src.modules.storage.models.memory import ArchiveLine
from src.modules.storage.repos._base import BaseRepo
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

# 档案行读面的列（与 ArchiveLine 字段一一对应）
_ARCHIVE_COLUMNS = (
    "id, live_session_id, text, quote, evidence_chat_id, game, save_id, save_name, from_session_id, lasting"
)


def delete_archive_lines_by_session(conn: sqlite3.Connection, *, live_session_id: int) -> int:
    """删除某一场的全部档案行，返回删除条数（场次改类型、删除场次时用）。

    模块级函数供跨表事务内的调用方复用同一条删除语句；ArchiveRepo 的异步方法
    只是它的事务包装。
    """
    cur = conn.execute("DELETE FROM archive_lines WHERE live_session_id=?", (int(live_session_id),))
    return int(cur.rowcount or 0)


def delete_archive_lines_from_session(conn: sqlite3.Connection, *, live_session_id: int) -> int:
    """删除某一场的档案行，连同滚动接进后面几场、出自这一场的副本，返回删除条数。

    场次改成彩排、删除场次时用：那一场里的事不该还留在后面几场的档案里。重新整理某一场
    不用它（那一场此前接进后面几场的行仍是当时成立的事，只替换这一场自己的档案）。
    """
    cur = conn.execute(
        "DELETE FROM archive_lines WHERE live_session_id=? OR from_session_id=?",
        (int(live_session_id), int(live_session_id)),
    )
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
        game: Optional[str] = None,
        save_id: str = "",
        save_name: str = "",
        from_session_id: Optional[int] = None,
        lasting: bool = False,
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
                    "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id, game, save_id,"
                    " save_name, from_session_id, lasting) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(live_session_id),
                        title,
                        quote_text,
                        int(evidence_chat_id),
                        game,
                        save_id or "",
                        save_name or "",
                        int(from_session_id) if from_session_id is not None else None,
                        1 if lasting else 0,
                    ),
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

    async def get_last_archived_session_id(
        self, *, kind: SessionKind = SESSION_KIND_LIVE, min_duration_ms: int = 0
    ) -> Optional[int]:
        """最近一个已整理、有档案行且属于该类型的场次主键；一场都没有返回 None。

        「上一场」由此定义：整理失败或当场没写出档案行的场次不算，主播不会把一场
        空场次当成上一场来回忆。``min_duration_ms`` 大于 0 时，时长不到它的场次也不算：
        开错后马上重开的那一小段只播了一两分钟，照样整理得出档案行，不跳过就会顶掉
        真正的上一场。时长按场次行的起止时刻算（保留期不删场次行，早年的场次也量得出）；
        没有结束时刻的场次量不出时长，按够长处理，不因此丢掉上一场。
        """

        def _exec() -> Optional[int]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT s.id FROM live_sessions s"
                    " JOIN archive_lines a ON a.live_session_id = s.id"
                    " WHERE s.kind=? AND (s.ended_at_ms IS NULL OR s.ended_at_ms - s.started_at_ms >= ?)"
                    " GROUP BY s.id ORDER BY s.started_at_ms DESC, s.id DESC LIMIT 1",
                    (kind, max(0, int(min_duration_ms))),
                ).fetchone()
                return int(row[0]) if row else None

        return await self._run_in_executor(_exec)

    async def list_rolling_base(
        self,
        *,
        before_started_at_ms: int,
        kind: SessionKind = SESSION_KIND_LIVE,
        max_legacy_sessions: int = 3,
    ) -> List[ArchiveLine]:
        """整理一场时要接着滚的上一份档案：开播早于 ``before_started_at_ms`` 的最近一份同类型档案。

        上一份已经是滚动档案（有行记着出自哪一场）就只取它——它已经接住了更早的事。上一份还是
        滚动之前的旧档案（每行都只有本场的事）时，往前再取几份旧档案，遇到滚动档案取完它就停，
        至多 ``max_legacy_sessions`` 份：开始滚动的第一场要把前几场仍然成立的事一起接住，不然
        升级前最后那一场如果是一场短的、乱的，更早的名字和住处就接不上了。
        不看场次时长：滚动档案本身就带着前面的事，短场次的那一份照样是最新的底稿。
        按场次从新到旧、每场内按写入顺序返回；同一句原话（证据台词行）只留最新那一份。
        """

        def _exec() -> List[ArchiveLine]:
            with self._manager.transaction() as conn:
                session_rows = conn.execute(
                    "SELECT s.id FROM live_sessions s"
                    " JOIN archive_lines a ON a.live_session_id = s.id"
                    " WHERE s.kind=? AND s.started_at_ms < ?"
                    " GROUP BY s.id ORDER BY s.started_at_ms DESC, s.id DESC LIMIT ?",
                    (kind, int(before_started_at_ms), max(1, int(max_legacy_sessions))),
                ).fetchall()
                base: List[ArchiveLine] = []
                seen: set[int] = set()
                for session_row in session_rows:
                    rows = conn.execute(
                        f"SELECT {_ARCHIVE_COLUMNS} FROM archive_lines WHERE live_session_id=? ORDER BY id ASC",  # noqa: S608
                        (int(session_row["id"]),),
                    ).fetchall()
                    lines = [ArchiveLine.model_validate(dict(row)) for row in rows]
                    for line in lines:
                        if int(line.evidence_chat_id) not in seen:
                            seen.add(int(line.evidence_chat_id))
                            base.append(line)
                    # 遇到滚动档案就停：它已经接住了更早的事
                    if any(line.from_session_id is not None for line in lines):
                        break
                return base

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


__all__ = ["ArchiveRepo", "delete_archive_lines_by_session", "delete_archive_lines_from_session"]
