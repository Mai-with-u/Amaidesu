"""ViewerCandidateRepo —— 观众候选（viewer_candidates）的读写：整理写进待审区，人批准或驳回。

两类候选：``viewer_defined`` 是观众定下、她当场接受却没复述的名字、设定、梗；``claimed_missing`` 是她说
"上次答应过""欠着的"、承诺账上却找不到的事。批准前对她完全不可见——简报、线索卡、回想、检索索引都
只读档案行与承诺，不读这张表。

- 整理写入：本场的待审候选整份替换（重新整理不会越堆越多），已批、已驳回的不动；
- 批准观众定下的：写成一行档案（原话是她那句回应的逐字片段，``source = viewer``，挂着那条弹幕，
  人锁住），放进最新一份快照的最前面（``pinned``）；
- 批准她说欠着的：登记成一条未了结承诺，证据是她说欠着的那一句，之后照常走了结；
- 驳回：留着这一行，同一条弹幕或同样的标题以后不再列（整理侧按 :meth:`rejected_keys` 去重）；
- 过期：待审的只按时间过期（列出后 14 天），不按场数。

批准、驳回都在同一事务里记一行 ``memory_edits``（``target = candidate``）。候选挂在两条台词记录上，
删观众、忘了我、按句删、删场次时随外键级联删除。
"""

from __future__ import annotations

import sqlite3
from typing import Any, List, Optional, Sequence, Tuple

from src.modules.storage.models.memory import PendingViewerCandidate, ViewerCandidate
from src.modules.storage.repos._base import BaseRepo
from src.modules.storage.repos.memory_review import (
    MAX_REVIEW_TITLE_CHARS,
    MemoryReviewError,
    insert_pinned_line,
    record_edit,
    stored_snapshot_owner,
)

# 待审候选只按时间过期：列出后这么久没人审就删（梗真有人在乎会被再提，停播几天也不会白丢）
CANDIDATE_EXPIRE_MS = 14 * 24 * 60 * 60 * 1000

VIEWER_DEFINED = "viewer_defined"
CLAIMED_MISSING = "claimed_missing"

_COLUMNS = (
    "id, live_session_id, kind, title, viewer_chat_id, viewer_quote, reply_chat_id, reply_quote, game, save_id,"
    " save_name, status, created_at_ms, decided_at_ms, archive_line_id, commitment_id"
)


def _candidate_of(row: Any) -> ViewerCandidate:
    return ViewerCandidate.model_validate(dict(row))


def replace_session_candidates(
    conn: sqlite3.Connection,
    *,
    live_session_id: int,
    candidates: Sequence[PendingViewerCandidate],
    created_at_ms: int,
) -> int:
    """在整理事务里写本场的待审候选：先删本场还在待审的，再写这一轮的；返回写了几条。

    已批、已驳回的不动（驳回的留作去重依据）。同一句回应只写一条。
    """
    conn.execute("DELETE FROM viewer_candidates WHERE live_session_id=? AND status='pending'", (int(live_session_id),))
    written = 0
    seen: set[int] = set()
    for item in candidates:
        if int(item.reply_chat_id) in seen:
            continue
        seen.add(int(item.reply_chat_id))
        conn.execute(
            "INSERT INTO viewer_candidates(live_session_id, kind, title, viewer_chat_id, viewer_quote, reply_chat_id,"
            " reply_quote, game, save_id, save_name, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(live_session_id),
                item.kind,
                item.title,
                item.viewer_chat_id,
                item.viewer_quote,
                int(item.reply_chat_id),
                item.reply_quote,
                item.game,
                item.save_id,
                item.save_name,
                int(created_at_ms),
            ),
        )
        written += 1
    return written


def _pending(conn: sqlite3.Connection, candidate_id: int) -> Any:
    """取一条待审候选；不存在报 not_found，已经审过报 conflict。"""
    row = conn.execute(f"SELECT {_COLUMNS} FROM viewer_candidates WHERE id=?", (int(candidate_id),)).fetchone()  # noqa: S608
    if row is None:
        raise MemoryReviewError("not_found", f"候选不存在: {candidate_id}")
    if row["status"] != "pending":
        raise MemoryReviewError("conflict", "这条候选已经审过了")
    return row


def _title(raw: Optional[str], fallback: str) -> str:
    """批准时可以改标题：没给就用整理写的那个；给了就校验长度。"""
    title = (raw if raw is not None else fallback).strip()
    if not title:
        raise MemoryReviewError("invalid", "标题不能为空")
    if len(title) > MAX_REVIEW_TITLE_CHARS:
        raise MemoryReviewError("invalid", f"标题至多 {MAX_REVIEW_TITLE_CHARS} 字")
    return title


class ViewerCandidateRepo(BaseRepo):
    """观众候选：待审区列表、批准、驳回、过期，以及整理去重用的驳回记录。"""

    async def list_candidates(
        self, *, status: Optional[str] = "pending", live_session_id: Optional[int] = None, limit: int = 200
    ) -> List[ViewerCandidate]:
        """候选列表（新的在前）；``status`` 为空不按状态筛。"""
        clauses: List[str] = []
        params: List[Any] = []
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        if live_session_id is not None:
            clauses.append("live_session_id=?")
            params.append(int(live_session_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        def _exec() -> List[ViewerCandidate]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM viewer_candidates{where} ORDER BY id DESC LIMIT ?",  # noqa: S608
                    (*params, max(1, int(limit))),
                ).fetchall()
            return [_candidate_of(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def count_pending(self) -> int:
        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT COUNT(*) FROM viewer_candidates WHERE status='pending'").fetchone()
            return int(row[0] or 0)

        return await self._run_in_executor(_exec)

    async def rejected_keys(self) -> Tuple[List[int], List[str]]:
        """驳回过的候选挂的弹幕与标题：整理时同一条弹幕或同样标题的不再列。"""

        def _exec() -> Tuple[List[int], List[str]]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT viewer_chat_id, title FROM viewer_candidates WHERE status='rejected'"
                ).fetchall()
            return [int(row[0]) for row in rows if row[0] is not None], [str(row[1]) for row in rows]

        return await self._run_in_executor(_exec)

    async def approve(
        self, *, candidate_id: int, title: Optional[str] = None, lasting: bool = False, at_ms: int
    ) -> ViewerCandidate:
        """批准一条候选，返回批准后的候选（带生成的档案行或承诺编号）。

        观众定下的写成一行观众来源的档案行并放进最新快照；她说欠着的登记成一条未了结承诺。
        还没有存下的快照、这句回应已经写成档案行或登记过承诺时做不了，给出原因。
        """

        def _exec() -> ViewerCandidate:
            with self._manager.transaction() as conn:
                row = _pending(conn, candidate_id)
                final = _title(title, str(row["title"]))
                line_id: Optional[int] = None
                commitment_id: Optional[int] = None
                if row["kind"] == CLAIMED_MISSING:
                    # 她说欠着的：登记成未了结承诺，证据是她说欠着的那一句（同一句只登记一次）
                    cur = conn.execute(
                        "INSERT INTO commitments(what, quote, evidence_chat_id, game, save_id, save_name,"
                        " created_session_id, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                        " ON CONFLICT(evidence_chat_id) DO NOTHING",
                        (
                            final,
                            row["reply_quote"],
                            row["reply_chat_id"],
                            row["game"],
                            row["save_id"],
                            row["save_name"],
                            row["live_session_id"],
                            int(at_ms),
                            int(at_ms),
                        ),
                    )
                    if not cur.rowcount:
                        raise MemoryReviewError("conflict", "这句台词已经登记过一条承诺了")
                    commitment_id = int(cur.lastrowid or 0)
                else:
                    # 观众定下的：原话是她那句回应的逐字片段，挂着那条弹幕；锁住并放进最新快照最前面
                    owner = stored_snapshot_owner(conn)
                    taken = conn.execute(
                        "SELECT 1 FROM archive_lines WHERE live_session_id=? AND evidence_chat_id=?",
                        (row["live_session_id"], row["reply_chat_id"]),
                    ).fetchone()
                    if taken is not None:
                        raise MemoryReviewError("conflict", "她这句回应已经写成一行档案了")
                    cur = conn.execute(
                        "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id, game, save_id,"
                        " save_name, keywords, place, human_locked, source, viewer_chat_id)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, '[]', '', 1, 'viewer', ?)",
                        (
                            row["live_session_id"],
                            final,
                            row["reply_quote"],
                            row["reply_chat_id"],
                            row["game"],
                            row["save_id"],
                            row["save_name"],
                            row["viewer_chat_id"],
                        ),
                    )
                    line_id = int(cur.lastrowid or 0)
                    insert_pinned_line(conn, owner=owner, line_id=line_id, lasting=lasting)
                conn.execute(
                    "UPDATE viewer_candidates SET status='approved', title=?, decided_at_ms=?, archive_line_id=?,"
                    " commitment_id=? WHERE id=?",
                    (final, int(at_ms), line_id, commitment_id, int(candidate_id)),
                )
                record_edit(
                    conn,
                    live_session_id=int(row["live_session_id"]),
                    target="candidate",
                    archive_line_id=line_id,
                    action="approve",
                    before=str(row["title"]),
                    after=final,
                    at_ms=at_ms,
                )
                done = conn.execute(
                    f"SELECT {_COLUMNS} FROM viewer_candidates WHERE id=?",  # noqa: S608
                    (int(candidate_id),),
                ).fetchone()
            return _candidate_of(done)

        return await self._run_in_executor(_exec)

    async def reject(self, *, candidate_id: int, at_ms: int) -> None:
        """驳回一条候选：留着这一行，同一条弹幕或同样的标题以后不再列。"""

        def _exec() -> None:
            with self._manager.transaction() as conn:
                row = _pending(conn, candidate_id)
                conn.execute(
                    "UPDATE viewer_candidates SET status='rejected', decided_at_ms=? WHERE id=?",
                    (int(at_ms), int(candidate_id)),
                )
                record_edit(
                    conn,
                    live_session_id=int(row["live_session_id"]),
                    target="candidate",
                    archive_line_id=None,
                    action="reject",
                    before=str(row["title"]),
                    after="",
                    at_ms=at_ms,
                )

        await self._run_in_executor(_exec)

    async def expire_pending(self, *, now_ms_value: int) -> int:
        """删掉列出超过 14 天还没人审的候选，返回删了几条。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "DELETE FROM viewer_candidates WHERE status='pending' AND created_at_ms < ?",
                    (int(now_ms_value) - CANDIDATE_EXPIRE_MS,),
                )
            return int(cur.rowcount or 0)

        return await self._run_in_executor(_exec)


__all__ = [
    "CANDIDATE_EXPIRE_MS",
    "CLAIMED_MISSING",
    "VIEWER_DEFINED",
    "ViewerCandidateRepo",
    "replace_session_candidates",
]
