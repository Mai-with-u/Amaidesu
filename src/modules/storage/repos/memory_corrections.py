"""MemoryCorrectionRepo —— 场内更正（memory_corrections）仓储（D2-4）。

负责本场记忆更正的写入、按场读取、下播整理处置回填，以及运营人工撤回（含还原被更正当场了结
或作废的承诺，并在 memory_edits 记一行审计留痕以供统计撤回率）。
"""

from __future__ import annotations

from typing import Dict, List, Literal, Mapping, Optional

from src.modules.storage.models.memory import MemoryCorrection
from src.modules.storage.repos._base import BaseRepo
from src.modules.storage.repos.memory_review import record_edit
from src.modules.utils.time_utils import now_ms

__all__ = [
    "CORRECTION_VERDICT_LABELS",
    "MemoryCorrectionRepo",
    "format_correction_note",
]

CORRECTION_VERDICT_LABELS: Dict[str, str] = {
    "done": "做完了",
    "invalid": "不成立了",
    "wrong": "记错了",
    "note": "运营纠正",
}

_COLUMNS = (
    "id, live_session_id, target_kind, archive_line_id, commitment_id, verdict, summary,"
    " evidence_kind, evidence_quote, evidence_game_chat_id, source, prev_commitment_status,"
    " created_at_ms, reverted_at_ms, settled_disposition"
)


def format_correction_note(verdict: str, summary: str) -> str:
    """拼装简报行尾展示的更正短句（如"记错了——树场已重开不存在"）。"""
    label = CORRECTION_VERDICT_LABELS.get(verdict, "更正")
    body = " ".join((summary or "").split())
    return f"{label}——{body}" if body else label


class MemoryCorrectionRepo(BaseRepo):
    """memory_corrections 表的读写与人工撤回事务面。"""

    async def insert_and_apply(
        self,
        *,
        live_session_id: int,
        target_kind: Literal["archive", "commitment", "general"],
        verdict: Literal["done", "invalid", "wrong", "note"],
        summary: str,
        evidence_kind: Literal["game_receipt", "player_chat", "operator", "self_seen"],
        evidence_quote: str = "",
        evidence_game_chat_id: Optional[int] = None,
        archive_line_id: Optional[int] = None,
        commitment_id: Optional[int] = None,
        source: Literal["streamer", "operator"] = "streamer",
        created_at_ms: Optional[int] = None,
    ) -> MemoryCorrection:
        """写入一条场内更正；若目标是未了结承诺则同一事务当场标了结或作废（记来源 correction）。"""
        ts = int(created_at_ms if created_at_ms is not None else now_ms())
        cleaned_summary = (summary or "").strip()
        cleaned_quote = (evidence_quote or "").strip()

        def _exec() -> MemoryCorrection:
            with self._manager.transaction(immediate=True) as conn:
                prev_status = ""
                if target_kind == "commitment" and commitment_id is not None:
                    row = conn.execute(
                        "SELECT status FROM commitments WHERE id=?",
                        (int(commitment_id),),
                    ).fetchone()
                    if row is not None and str(row[0]) == "open":
                        new_status = "void" if verdict == "wrong" else "closed"
                        close_quote = (
                            f"场内更正（{CORRECTION_VERDICT_LABELS.get(verdict, verdict)}）：{cleaned_summary}"
                        )
                        updated = conn.execute(
                            "UPDATE commitments SET status=?, close_quote=?, close_source='correction', updated_at_ms=?"
                            " WHERE id=? AND status='open'",
                            (
                                new_status,
                                close_quote[:80] if new_status == "closed" else None,
                                ts,
                                int(commitment_id),
                            ),
                        )
                        if updated.rowcount > 0:
                            prev_status = "open"
                cur = conn.execute(
                    "INSERT INTO memory_corrections("
                    " live_session_id, target_kind, archive_line_id, commitment_id, verdict, summary,"
                    " evidence_kind, evidence_quote, evidence_game_chat_id, source, prev_commitment_status,"
                    " created_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(live_session_id),
                        target_kind,
                        int(archive_line_id) if archive_line_id is not None else None,
                        int(commitment_id) if commitment_id is not None else None,
                        verdict,
                        cleaned_summary,
                        evidence_kind,
                        cleaned_quote,
                        int(evidence_game_chat_id) if evidence_game_chat_id is not None else None,
                        source,
                        prev_status,
                        ts,
                    ),
                )
                new_id = int(cur.lastrowid or 0)
                fetched = conn.execute(
                    f"SELECT {_COLUMNS} FROM memory_corrections WHERE id=?",  # noqa: S608 列为代码内常量
                    (new_id,),
                ).fetchone()
                return MemoryCorrection.model_validate(dict(fetched))

        return await self._run_in_executor(_exec)

    async def record_operator_note(
        self,
        *,
        live_session_id: int,
        content: str,
        created_at_ms: Optional[int] = None,
    ) -> Optional[MemoryCorrection]:
        """将运营递话落为一条本场常驻纠正（空文本忽略）。"""
        body = (content or "").strip()
        if not body:
            return None
        return await self.insert_and_apply(
            live_session_id=int(live_session_id),
            target_kind="general",
            verdict="note",
            summary=body[:120],
            evidence_kind="operator",
            evidence_quote=body[:80],
            source="operator",
            created_at_ms=created_at_ms,
        )

    async def list_active_by_session(self, *, live_session_id: int) -> List[MemoryCorrection]:
        """读取某场全部生效中（未被人工撤回）的更正，按时间升序。"""

        def _exec() -> List[MemoryCorrection]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM memory_corrections"  # noqa: S608 列为代码内常量
                    " WHERE live_session_id=? AND reverted_at_ms IS NULL"
                    " ORDER BY created_at_ms ASC, id ASC",
                    (int(live_session_id),),
                ).fetchall()
                return [MemoryCorrection.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def list_by_session(self, *, live_session_id: int) -> List[MemoryCorrection]:
        """读取某场全部更正（含已撤回的），按时间升序，供管理面查看与算指标。"""

        def _exec() -> List[MemoryCorrection]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM memory_corrections"  # noqa: S608 列为代码内常量
                    " WHERE live_session_id=? ORDER BY created_at_ms ASC, id ASC",
                    (int(live_session_id),),
                ).fetchall()
                return [MemoryCorrection.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def revert_correction(
        self,
        *,
        correction_id: int,
        reverted_at_ms: Optional[int] = None,
    ) -> bool:
        """人工撤回一条场内更正；若它曾把 open 承诺标为 closed/void 且未被人工覆盖，则还原承诺为 open。"""
        ts = int(reverted_at_ms if reverted_at_ms is not None else now_ms())

        def _exec() -> bool:
            with self._manager.transaction(immediate=True) as conn:
                row = conn.execute(
                    f"SELECT {_COLUMNS} FROM memory_corrections WHERE id=?",  # noqa: S608 列为代码内常量
                    (int(correction_id),),
                ).fetchone()
                if row is None:
                    return False
                item = MemoryCorrection.model_validate(dict(row))
                if item.reverted_at_ms is not None:
                    return False
                conn.execute(
                    "UPDATE memory_corrections SET reverted_at_ms=? WHERE id=?",
                    (ts, int(correction_id)),
                )
                if (
                    item.target_kind == "commitment"
                    and item.commitment_id is not None
                    and item.prev_commitment_status == "open"
                ):
                    conn.execute(
                        "UPDATE commitments SET status='open', close_chat_id=NULL, close_quote=NULL,"
                        " close_source='', updated_at_ms=?"
                        " WHERE id=? AND close_source='correction'",
                        (ts, int(item.commitment_id)),
                    )
                record_edit(
                    conn,
                    live_session_id=int(item.live_session_id),
                    target="correction",
                    archive_line_id=item.archive_line_id,
                    action="revert_correction",
                    before=item.summary,
                    after="",
                    at_ms=ts,
                )
                return True

        return await self._run_in_executor(_exec)

    async def mark_settled_dispositions(
        self,
        *,
        dispositions: Mapping[int, str],
    ) -> int:
        """下播整理完成后回填每条更正的整理结果（applied / unhandled）。"""
        if not dispositions:
            return 0
        pairs = [(str(val), int(cid)) for cid, val in dispositions.items()]

        def _exec() -> int:
            changed = 0
            with self._manager.transaction() as conn:
                for val, cid in pairs:
                    cur = conn.execute(
                        "UPDATE memory_corrections SET settled_disposition=? WHERE id=?",
                        (val, cid),
                    )
                    changed += max(0, int(cur.rowcount or 0))
            return changed

        return await self._run_in_executor(_exec)
