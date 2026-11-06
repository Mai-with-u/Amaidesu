"""MemoryReviewRepo —— 记忆页人工审核：改标题、标稳定、放回快照、新增一行、删行、标已审。

整理照旧自动生效、不等人审（否则哪场没审她那场就失忆）；运营者事后在记忆页改：

- 改标题：改档案行的标题，置 ``human_locked``；
- 标或取消稳定：改最新一份快照里这一行的 ``lasting``，置 ``human_locked``；
- 放回快照：被挤出或被去掉的行插回最新一份快照的最前面，``pinned = 1``；
- 新增一行：从某一场她播出的台词里截一段逐字原话、写标题，新建档案行（``human_locked = 1``），
  放进最新一份快照（``pinned = 1``）；
- 删行：删档案行（外键级联撤出所有快照，检索索引由触发器一起删）；
- 标已审：写场次的 ``reviewed_at_ms``，只是状态。

人改过的行，之后的整理只能保留（去掉、替换都被拒绝）；放回、新增的行取舍时排第 0 档，整理不挤掉。
每次操作在同一事务里写一行 ``memory_edits``（审计，同时是整理评测的标准答案）：``target`` 是
``archive_line``（档案行本身）、``snapshot_line``（最新快照里的那一行）或 ``session``（场次），
``action`` 是 ``title`` / ``lasting`` / ``pin`` / ``add`` / ``delete`` / ``reviewed``。

"最新一份快照"是下一场整理拿来当基底的那一份：开播最晚的、写过快照或有档案行的正式场。它是滚动
之前的旧场次（没存下快照）时，放回、标稳定、新增都做不了，等下一场整理写出快照再来。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.modules.storage.repos._base import BaseRepo
from src.modules.storage.repos.archive import newest_snapshot_owner
from src.modules.types.session_kind import SESSION_KIND_LIVE

# 和整理写的档案行同一上限：标题至多 30 字、原话至多 80 字
MAX_REVIEW_TITLE_CHARS = 30
MAX_REVIEW_QUOTE_CHARS = 80


class MemoryReviewError(Exception):
    """审核操作做不了。``kind`` 是 not_found / conflict / invalid，``message`` 直接给页面看。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class ReviewSnapshotLine:
    """最新一份快照里的一行：是不是稳定的事、是不是人放回或新增的。"""

    archive_line_id: int
    lasting: bool
    pinned: bool


@dataclass(frozen=True)
class SessionReviewState:
    """某一场在记忆页上的审核状态，连同最新一份快照的样子（判断每行在不在快照里）。"""

    live_session_id: int
    reviewed_at_ms: Optional[int]
    settled_at_ms: Optional[int]
    # 最新一份快照所在的场次；None = 还没有存下的快照（放回、标稳定、新增做不了）
    snapshot_session_id: Optional[int]
    snapshot_lines: Dict[int, ReviewSnapshotLine] = field(default_factory=dict)
    # 最新快照里人放回、新增的行数：超过 12 行时界面提示人去减（整理不会替人挤掉）
    pinned_count: int = 0


def _record(
    conn: sqlite3.Connection,
    *,
    live_session_id: int,
    target: str,
    archive_line_id: Optional[int],
    action: str,
    before: str,
    after: str,
    at_ms: int,
) -> None:
    """写一行改动记录（与改动同一事务）。"""
    conn.execute(
        "INSERT INTO memory_edits(live_session_id, target, archive_line_id, action, before_text, after_text, at_ms)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(live_session_id), target, archive_line_id, action, before, after, int(at_ms)),
    )


def _line(conn: sqlite3.Connection, line_id: int) -> Any:
    """取一行档案；不存在就报 not_found。"""
    row = conn.execute(
        "SELECT id, live_session_id, text, human_locked FROM archive_lines WHERE id=?", (int(line_id),)
    ).fetchone()
    if row is None:
        raise MemoryReviewError("not_found", f"档案行不存在: {line_id}")
    return row


def _stored_owner(conn: sqlite3.Connection) -> int:
    """最新一份快照所在的场次；没有存下的快照就报 conflict。"""
    owner = newest_snapshot_owner(conn, SESSION_KIND_LIVE)
    if owner is None or not owner[1]:
        raise MemoryReviewError("conflict", "还没有存下的快照：等下一场整理写出快照后再改")
    return owner[0]


def _snapshot_row(conn: sqlite3.Connection, owner: int, line_id: int) -> Optional[Any]:
    return conn.execute(
        "SELECT lasting, pinned FROM archive_snapshot_lines WHERE live_session_id=? AND archive_line_id=?",
        (int(owner), int(line_id)),
    ).fetchone()


def _insert_pinned(conn: sqlite3.Connection, *, owner: int, line_id: int, lasting: bool) -> None:
    """把一行插进最新快照的最前面并标放回：常驻简报按位置列，满 12 行时它也排得上。"""
    top = conn.execute(
        "SELECT MIN(position) FROM archive_snapshot_lines WHERE live_session_id=?", (int(owner),)
    ).fetchone()
    position = (int(top[0]) if top is not None and top[0] is not None else 0) - 1
    started = conn.execute("SELECT started_at_ms FROM live_sessions WHERE id=?", (int(owner),)).fetchone()
    conn.execute(
        "INSERT INTO archive_snapshot_lines(live_session_id, archive_line_id, position, lasting, seen_at_ms, pinned)"
        " VALUES (?, ?, ?, ?, ?, 1)",
        (int(owner), int(line_id), position, 1 if lasting else 0, int(started[0] or 0) if started else 0),
    )


def _clean_title(text: str) -> str:
    title = (text or "").strip()
    if not title:
        raise MemoryReviewError("invalid", "标题不能为空")
    if len(title) > MAX_REVIEW_TITLE_CHARS:
        raise MemoryReviewError("invalid", f"标题至多 {MAX_REVIEW_TITLE_CHARS} 字")
    return title


def _settled_live_session(conn: sqlite3.Connection, live_session_id: int) -> Any:
    """取一场已整理完的正式场；不存在报 not_found，彩排或还没整理完报 conflict。"""
    row = conn.execute(
        "SELECT id, kind, settled_at_ms FROM live_sessions WHERE id=?", (int(live_session_id),)
    ).fetchone()
    if row is None:
        raise MemoryReviewError("not_found", f"场次不存在: {live_session_id}")
    if row["kind"] != SESSION_KIND_LIVE:
        raise MemoryReviewError("conflict", "彩排场不整理，没有档案可审")
    if row["settled_at_ms"] is None:
        # 没整理完的场次整理时会整场替换档案行：人这时加的行会被冲掉
        raise MemoryReviewError("conflict", "这一场还没整理完，等整理写完再改")
    return row


class MemoryReviewRepo(BaseRepo):
    """记忆页人工审核的读写面（每次改动连同改动记录一个事务写完）。"""

    async def session_state(self, *, live_session_id: int) -> SessionReviewState:
        """某一场的审核状态与最新一份快照里的各行；场次不存在时审核状态为空。"""

        def _exec() -> SessionReviewState:
            with self._manager.transaction() as conn:
                session = conn.execute(
                    "SELECT reviewed_at_ms, settled_at_ms FROM live_sessions WHERE id=?", (int(live_session_id),)
                ).fetchone()
                owner = newest_snapshot_owner(conn, SESSION_KIND_LIVE)
                snapshot_id = owner[0] if owner is not None and owner[1] else None
                lines: Dict[int, ReviewSnapshotLine] = {}
                if snapshot_id is not None:
                    for row in conn.execute(
                        "SELECT archive_line_id, lasting, pinned FROM archive_snapshot_lines WHERE live_session_id=?",
                        (int(snapshot_id),),
                    ).fetchall():
                        lines[int(row[0])] = ReviewSnapshotLine(
                            archive_line_id=int(row[0]), lasting=bool(row[1]), pinned=bool(row[2])
                        )
                return SessionReviewState(
                    live_session_id=int(live_session_id),
                    reviewed_at_ms=session["reviewed_at_ms"] if session is not None else None,
                    settled_at_ms=session["settled_at_ms"] if session is not None else None,
                    snapshot_session_id=snapshot_id,
                    snapshot_lines=lines,
                    pinned_count=sum(1 for item in lines.values() if item.pinned),
                )

        return await self._run_in_executor(_exec)

    async def edit_title(self, *, line_id: int, text: str, at_ms: int) -> bool:
        """改一行档案的标题并锁住；标题没变返回 False（不写记录）。"""
        title = _clean_title(text)

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                row = _line(conn, line_id)
                if row["text"] == title:
                    return False
                conn.execute("UPDATE archive_lines SET text=?, human_locked=1 WHERE id=?", (title, int(line_id)))
                _record(
                    conn,
                    live_session_id=int(row["live_session_id"]),
                    target="archive_line",
                    archive_line_id=int(line_id),
                    action="title",
                    before=str(row["text"]),
                    after=title,
                    at_ms=at_ms,
                )
                return True

        return await self._run_in_executor(_exec)

    async def set_lasting(self, *, line_id: int, lasting: bool, at_ms: int) -> None:
        """标或取消最新快照里这一行是稳定的事，并锁住这一行（之后按人标的算）。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                _line(conn, line_id)
                owner = _stored_owner(conn)
                current = _snapshot_row(conn, owner, line_id)
                if current is None:
                    raise MemoryReviewError("conflict", "这一行不在最新一份快照里：先放回快照再标")
                conn.execute(
                    "UPDATE archive_snapshot_lines SET lasting=? WHERE live_session_id=? AND archive_line_id=?",
                    (1 if lasting else 0, int(owner), int(line_id)),
                )
                conn.execute("UPDATE archive_lines SET human_locked=1 WHERE id=?", (int(line_id),))
                _record(
                    conn,
                    live_session_id=owner,
                    target="snapshot_line",
                    archive_line_id=int(line_id),
                    action="lasting",
                    before="1" if current["lasting"] else "0",
                    after="1" if lasting else "0",
                    at_ms=at_ms,
                )

        await self._run_in_executor(_exec)

    async def pin_line(self, *, line_id: int, at_ms: int) -> None:
        """把被挤出或被去掉的一行放回最新快照（排最前、标放回）；稳定与否沿用它上一次在快照里的标记。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                row = _line(conn, line_id)
                owner = _stored_owner(conn)
                if _snapshot_row(conn, owner, line_id) is not None:
                    raise MemoryReviewError("conflict", "这一行已经在最新一份快照里了")
                last = conn.execute(
                    "SELECT l.lasting FROM archive_snapshot_lines l JOIN live_sessions s ON s.id = l.live_session_id"
                    " WHERE l.archive_line_id=? ORDER BY s.started_at_ms DESC, s.id DESC LIMIT 1",
                    (int(line_id),),
                ).fetchone()
                _insert_pinned(conn, owner=owner, line_id=int(line_id), lasting=bool(last[0]) if last else False)
                _record(
                    conn,
                    live_session_id=owner,
                    target="snapshot_line",
                    archive_line_id=int(line_id),
                    action="pin",
                    before="",
                    after=str(row["text"]),
                    at_ms=at_ms,
                )

        await self._run_in_executor(_exec)

    async def add_line(
        self,
        *,
        live_session_id: int,
        evidence_chat_id: int,
        quote: str,
        text: str,
        lasting: bool,
        at_ms: int,
    ) -> int:
        """从这一场她播出的一句台词里截一段逐字原话、写标题，新建一行档案并放进最新快照，返回主键。

        原话要逐字出自那一句（连续空白压成一个空格后的写法也认，和整理同一口径）；同一句台词
        已经写成档案行的不再写第二行。这一行锁住、标放回，下一场整理不会去掉或挤掉它。
        """
        title = _clean_title(text)
        quote_text = (quote or "").strip()
        if not quote_text:
            raise MemoryReviewError("invalid", "原话不能为空")
        if len(quote_text) > MAX_REVIEW_QUOTE_CHARS:
            raise MemoryReviewError("invalid", f"原话至多 {MAX_REVIEW_QUOTE_CHARS} 字")

        def _exec() -> int:
            with self._manager.transaction() as conn:
                _settled_live_session(conn, live_session_id)
                speech = conn.execute(
                    "SELECT live_session_id, sender_role, content, dropped_at_ms FROM live_chat WHERE id=?",
                    (int(evidence_chat_id),),
                ).fetchone()
                if (
                    speech is None
                    or int(speech["live_session_id"]) != int(live_session_id)
                    or speech["sender_role"] != "assistant"
                    or speech["dropped_at_ms"] is not None
                ):
                    raise MemoryReviewError("invalid", "这句不是这一场她播出的台词")
                content = str(speech["content"] or "")
                if quote_text not in content and quote_text not in " ".join(content.split()):
                    raise MemoryReviewError("invalid", "原话要从那句台词里逐字截取")
                taken = conn.execute(
                    "SELECT 1 FROM archive_lines WHERE live_session_id=? AND evidence_chat_id=?",
                    (int(live_session_id), int(evidence_chat_id)),
                ).fetchone()
                if taken is not None:
                    raise MemoryReviewError("conflict", "这句台词已经写成一行档案了，改那一行的标题即可")
                owner = _stored_owner(conn)
                cur = conn.execute(
                    "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id, save_id, save_name,"
                    " keywords, place, human_locked) VALUES (?, ?, ?, ?, '', '', '[]', '', 1)",
                    (int(live_session_id), title, quote_text, int(evidence_chat_id)),
                )
                new_id = int(cur.lastrowid or 0)
                _insert_pinned(conn, owner=owner, line_id=new_id, lasting=lasting)
                _record(
                    conn,
                    live_session_id=int(live_session_id),
                    target="archive_line",
                    archive_line_id=new_id,
                    action="add",
                    before="",
                    after=title,
                    at_ms=at_ms,
                )
                return new_id

        return await self._run_in_executor(_exec)

    async def delete_line(self, *, line_id: int, at_ms: int) -> bool:
        """删一行档案（级联撤出所有快照），记一笔只有动作与时刻的改动记录；行不存在返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT live_session_id FROM archive_lines WHERE id=?", (int(line_id),)).fetchone()
                if row is None:
                    return False
                conn.execute("DELETE FROM archive_lines WHERE id=?", (int(line_id),))
                # 被删的行连文本也不留：改动记录只记删了哪一行、什么时候删的
                _record(
                    conn,
                    live_session_id=int(row[0]),
                    target="archive_line",
                    archive_line_id=int(line_id),
                    action="delete",
                    before="",
                    after="",
                    at_ms=at_ms,
                )
                return True

        return await self._run_in_executor(_exec)

    async def mark_reviewed(self, *, live_session_id: int, at_ms: int) -> None:
        """把一场标成已审（只是状态，不改记忆）。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                _settled_live_session(conn, live_session_id)
                conn.execute("UPDATE live_sessions SET reviewed_at_ms=? WHERE id=?", (int(at_ms), int(live_session_id)))
                _record(
                    conn,
                    live_session_id=int(live_session_id),
                    target="session",
                    archive_line_id=None,
                    action="reviewed",
                    before="",
                    after="",
                    at_ms=at_ms,
                )

        await self._run_in_executor(_exec)

    async def count_human_edits(self, *, live_session_id: int) -> int:
        """这一场重新整理会冲掉的人工改动：本场人锁住的档案行，加上本场快照里人放回、新增的行。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                locked = conn.execute(
                    "SELECT COUNT(*) FROM archive_lines WHERE live_session_id=? AND human_locked=1",
                    (int(live_session_id),),
                ).fetchone()
                pinned = conn.execute(
                    "SELECT COUNT(*) FROM archive_snapshot_lines WHERE live_session_id=? AND pinned=1",
                    (int(live_session_id),),
                ).fetchone()
                return int(locked[0] or 0) + int(pinned[0] or 0)

        return await self._run_in_executor(_exec)


__all__ = [
    "MAX_REVIEW_QUOTE_CHARS",
    "MAX_REVIEW_TITLE_CHARS",
    "MemoryReviewError",
    "MemoryReviewRepo",
    "ReviewSnapshotLine",
    "SessionReviewState",
]
