"""ArchiveRepo —— 场次档案仓储（archive_lines 与滚动档案快照）。

每场正式场下播整理写 ≤8 行本场新事，标题加她播出原句的逐字片段，写完不改；运营者可以删单行。
她下次开播该记得的事由快照给出：每个整理过的正式场留一份快照（``archive_snapshots`` /
``archive_snapshot_lines``），引用这一场新写的行与前几场仍然成立的行，至多 12 行。快照只引用
原行、不复制：原行被删时外键级联撤掉所有快照里的引用，删观众替换原行文本时快照看到的也是
替换后的文本，回想工具遮名照样生效。

滚动之前的旧场次没有存下的快照，由它与更早几场的档案行现拼（:func:`_legacy_snapshot`），
升级后的第一次整理从这里接起。常驻简报取最近一个时长够的场次的快照
（:meth:`ArchiveRepo.latest_snapshot`），整理取开播早于本场的最近一份快照作基底
（:meth:`ArchiveRepo.snapshot_before`），回想工具按场次查各场自己的档案行。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, List, Optional, Set, Tuple

from src.modules.storage.models.memory import ArchiveLine, ArchiveSnapshot, SnapshotLine
from src.modules.storage.repos._base import BaseRepo
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

# 档案行读面的列（与 ArchiveLine 字段一一对应）
_ARCHIVE_COLUMNS = "id, live_session_id, text, quote, evidence_chat_id, game, save_id, save_name"
# 快照读面：引用的档案行连同快照标注一起取
_SNAPSHOT_COLUMNS = (
    "a.id, a.live_session_id, a.text, a.quote, a.evidence_chat_id, a.game, a.save_id, a.save_name,"
    " l.lasting AS snap_lasting, l.seen_at_ms AS snap_seen_at_ms"
)
# 旧场次现拼快照：连着往前至多取几场有档案行的旧场次，其中凑够几场时长够的就停
# （升级前最后一场如果是开错重开的一小段，也能接到真正的上一场和再前一场）
LEGACY_SNAPSHOT_MAX_SESSIONS = 3
LEGACY_SNAPSHOT_FULL_SESSIONS = 2

# 候选场次：同类型、有快照或有档案行（只有整理会写这两样，所以都整理过）
_CANDIDATE_SQL = (
    "SELECT s.id, s.started_at_ms, s.ended_at_ms,"
    " EXISTS(SELECT 1 FROM archive_snapshots p WHERE p.live_session_id = s.id) AS stored"
    " FROM live_sessions s WHERE s.kind=?"
    " AND (EXISTS(SELECT 1 FROM archive_snapshots p WHERE p.live_session_id = s.id)"
    " OR EXISTS(SELECT 1 FROM archive_lines a WHERE a.live_session_id = s.id))"
)


@dataclass(frozen=True)
class _Candidate:
    """一个候选场次：主键、起止时刻、是否存下了快照。"""

    session_id: int
    started_at_ms: int
    ended_at_ms: Optional[int]
    stored: bool


def _candidates(conn: sqlite3.Connection, where: str, params: Tuple[Any, ...]) -> List[_Candidate]:
    """按附加条件取候选场次（开播时刻从新到旧）。"""
    rows = conn.execute(_CANDIDATE_SQL + where + " ORDER BY s.started_at_ms DESC, s.id DESC", params).fetchall()
    return [
        _Candidate(
            session_id=int(row[0]),
            started_at_ms=int(row[1] or 0),
            ended_at_ms=int(row[2]) if row[2] is not None else None,
            stored=bool(row[3]),
        )
        for row in rows
    ]


def delete_archive_lines_by_session(conn: sqlite3.Connection, *, live_session_id: int) -> int:
    """删除某一场的全部档案行，返回删除条数（场次改类型、删除场次、重新整理时用）。

    模块级函数供跨表事务内的调用方复用同一条删除语句；ArchiveRepo 的异步方法
    只是它的事务包装。被删的行由外键级联从所有快照里撤掉。
    """
    cur = conn.execute("DELETE FROM archive_lines WHERE live_session_id=?", (int(live_session_id),))
    return int(cur.rowcount or 0)


def delete_snapshot(conn: sqlite3.Connection, *, live_session_id: int) -> None:
    """删除某一场写下的快照（连同它的各行引用；被引用的档案行不动）。"""
    conn.execute("DELETE FROM archive_snapshots WHERE live_session_id=?", (int(live_session_id),))


def _long_enough(candidate: _Candidate, min_duration_ms: int) -> bool:
    """场次时长够不够：没有结束时刻的量不出时长，按够长处理。"""
    if candidate.ended_at_ms is None:
        return True
    return candidate.ended_at_ms - candidate.started_at_ms >= max(0, int(min_duration_ms))


def _stored_lines(conn: sqlite3.Connection, live_session_id: int) -> List[SnapshotLine]:
    """某一场存下的快照各行（按位置）。"""
    rows = conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM archive_snapshot_lines l"  # noqa: S608 列名为代码内常量
        " JOIN archive_lines a ON a.id = l.archive_line_id"
        " WHERE l.live_session_id=? ORDER BY l.position ASC, a.id ASC",
        (int(live_session_id),),
    ).fetchall()
    result: List[SnapshotLine] = []
    for row in rows:
        data = dict(row)
        lasting = bool(data.pop("snap_lasting"))
        seen_at_ms = int(data.pop("snap_seen_at_ms"))
        result.append(SnapshotLine(line=ArchiveLine.model_validate(data), lasting=lasting, seen_at_ms=seen_at_ms))
    return result


def _own_lines(conn: sqlite3.Connection, candidate: _Candidate) -> List[SnapshotLine]:
    """一个旧场次自己的档案行，按快照的样子带出：不算稳定的事，最近一次提起就是那一场。"""
    rows = conn.execute(
        f"SELECT {_ARCHIVE_COLUMNS} FROM archive_lines WHERE live_session_id=? ORDER BY id ASC",  # noqa: S608
        (candidate.session_id,),
    ).fetchall()
    return [
        SnapshotLine(line=ArchiveLine.model_validate(dict(item)), seen_at_ms=candidate.started_at_ms) for item in rows
    ]


def _legacy_snapshot(
    conn: sqlite3.Connection, start: _Candidate, kind: SessionKind, min_duration_ms: int
) -> List[SnapshotLine]:
    """滚动之前的旧场次现拼快照：它自己的行，加往前几场的档案行（同一句原话只留最新的一行）。

    往前遇到存下快照的场次就接上它的快照并停；否则连着取旧场次，取满
    :data:`LEGACY_SNAPSHOT_MAX_SESSIONS` 场或其中时长够的凑满 :data:`LEGACY_SNAPSHOT_FULL_SESSIONS`
    场就停。
    """
    lines = _own_lines(conn, start)
    sessions = 1
    full = 1 if _long_enough(start, min_duration_ms) else 0
    for candidate in _candidates(conn, " AND s.started_at_ms < ?", (kind, start.started_at_ms)):
        if sessions >= LEGACY_SNAPSHOT_MAX_SESSIONS or full >= LEGACY_SNAPSHOT_FULL_SESSIONS:
            break
        if candidate.stored:
            lines.extend(_stored_lines(conn, candidate.session_id))
            break
        lines.extend(_own_lines(conn, candidate))
        sessions += 1
        full += 1 if _long_enough(candidate, min_duration_ms) else 0
    seen: Set[int] = set()
    unique: List[SnapshotLine] = []
    for item in lines:
        if int(item.line.evidence_chat_id) in seen:
            continue
        seen.add(int(item.line.evidence_chat_id))
        unique.append(item)
    return unique


def _snapshot_of(
    conn: sqlite3.Connection, candidate: _Candidate, kind: SessionKind, min_duration_ms: int
) -> ArchiveSnapshot:
    """一个候选场次的快照：存下的就照存的读，旧场次现拼。"""
    if candidate.stored:
        return ArchiveSnapshot(owner_session_id=candidate.session_id, lines=_stored_lines(conn, candidate.session_id))
    return ArchiveSnapshot(
        owner_session_id=candidate.session_id,
        lines=_legacy_snapshot(conn, candidate, kind, min_duration_ms),
        stored=False,
    )


def _latest_long_enough(conn: sqlite3.Connection, kind: SessionKind, min_duration_ms: int) -> Optional[_Candidate]:
    """最近一个时长够的候选场次（常驻简报与承诺卡的"上一场"）。"""
    for candidate in _candidates(conn, "", (kind,)):
        if _long_enough(candidate, min_duration_ms):
            return candidate
    return None


class ArchiveRepo(BaseRepo):
    """archive_lines 与档案快照的读写面。"""

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
    ) -> int:
        """写一行档案，返回主键；标题或原话为空时不写（返回 0）。

        档案行自带原话作证据，两者缺一就失去意义（调用方给了空白串也不入库）；
        内容与原话都去首尾空白后再写。不写快照：整理的快照由 ``apply_settlement`` 一起写。
        """
        title = (text or "").strip()
        quote_text = (quote or "").strip()
        if not title or not quote_text:
            return 0

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO archive_lines(live_session_id, text, quote, evidence_chat_id, game, save_id, save_name)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(live_session_id),
                        title,
                        quote_text,
                        int(evidence_chat_id),
                        game,
                        save_id or "",
                        save_name or "",
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_lines(self, *, live_session_id: int) -> List[ArchiveLine]:
        """某一场自己的档案行（写入顺序：整理时按模型给出的次序写）。"""

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
        """「上一场」的场次主键：最近一个写过快照或有档案行、时长够的同类型场次；没有返回 None。

        整理失败或当场什么都没写下的场次不算，主播不会把一场空场次当成上一场来回忆。
        ``min_duration_ms`` 大于 0 时，时长不到它的场次也不算：开错后马上重开的那一小段
        照样整理得出档案，不跳过就会顶掉真正的上一场（它的快照照样是下一次整理的基底）。
        时长按场次行的起止时刻算；没有结束时刻的场次量不出时长，按够长处理。
        """

        def _exec() -> Optional[int]:
            with self._manager.transaction() as conn:
                candidate = _latest_long_enough(conn, kind, min_duration_ms)
                return candidate.session_id if candidate is not None else None

        return await self._run_in_executor(_exec)

    async def latest_snapshot(
        self, *, kind: SessionKind = SESSION_KIND_LIVE, min_duration_ms: int = 0
    ) -> Optional[ArchiveSnapshot]:
        """常驻简报读的快照：「上一场」（口径同 :meth:`get_last_archived_session_id`）的快照。"""

        def _exec() -> Optional[ArchiveSnapshot]:
            with self._manager.transaction() as conn:
                candidate = _latest_long_enough(conn, kind, min_duration_ms)
                return _snapshot_of(conn, candidate, kind, min_duration_ms) if candidate is not None else None

        return await self._run_in_executor(_exec)

    async def snapshot_before(
        self,
        *,
        before_started_at_ms: int,
        kind: SessionKind = SESSION_KIND_LIVE,
        min_duration_ms: int = 0,
    ) -> Optional[ArchiveSnapshot]:
        """整理一场时的基底：开播早于 ``before_started_at_ms`` 的最近一份同类型快照；没有返回 None。

        不看这一份所在场次的时长：短场次的快照照样带着前面几场的事，是最新的一份。
        ``min_duration_ms`` 只用于旧场次现拼时数"时长够的场次"。
        """

        def _exec() -> Optional[ArchiveSnapshot]:
            with self._manager.transaction() as conn:
                earlier = _candidates(conn, " AND s.started_at_ms < ?", (kind, int(before_started_at_ms)))
                return _snapshot_of(conn, earlier[0], kind, min_duration_ms) if earlier else None

        return await self._run_in_executor(_exec)

    async def get_snapshot(self, *, live_session_id: int) -> Optional[ArchiveSnapshot]:
        """某一场存下的快照；这一场没写过快照返回 None（不现拼）。"""

        def _exec() -> Optional[ArchiveSnapshot]:
            with self._manager.transaction() as conn:
                stored = conn.execute(
                    "SELECT 1 FROM archive_snapshots WHERE live_session_id=?", (int(live_session_id),)
                ).fetchone()
                if stored is None:
                    return None
                return ArchiveSnapshot(
                    owner_session_id=int(live_session_id), lines=_stored_lines(conn, int(live_session_id))
                )

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
        """运营者删除一行档案（连同所有快照里对它的引用）；行不存在返回 False。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM archive_lines WHERE id=?", (int(line_id),))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_by_session(self, *, live_session_id: int) -> int:
        """删除某一场的全部档案行与它写下的快照，返回删除的档案行条数。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                delete_snapshot(conn, live_session_id=int(live_session_id))
                return delete_archive_lines_by_session(conn, live_session_id=int(live_session_id))

        return await self._run_in_executor(_exec)


__all__ = [
    "LEGACY_SNAPSHOT_FULL_SESSIONS",
    "LEGACY_SNAPSHOT_MAX_SESSIONS",
    "ArchiveRepo",
    "delete_archive_lines_by_session",
    "delete_snapshot",
]
