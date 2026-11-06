"""MemoryCueLogRepo —— 线索卡记录（memory_cue_log）的读写。

每张推给 Planner 的线索卡记一行：哪一场、什么时刻、什么卡（对象开口 / 游戏开着 / 地点 / 规则 /
向量 / 老梗）、指向哪条档案行或承诺、命中它的全部来源、分数明细、是不是只由她自己的台词触发，
以及她什么时候用上了（"用上即停"检测到时回填）。复盘面板与副作用指标都从这里读。

只记条目编号和分数，不记观众身份（不写昵称、外部 ID、弹幕编号）；指向的档案行或承诺被删时，
外键级联删掉对应记录，场次被删时整场记录跟着删。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Sequence

from src.modules.logging import get_logger
from src.modules.storage.models.memory import CueLogEntry, NewCueLogEntry
from src.modules.storage.repos._base import BaseRepo

logger = get_logger("MemoryCueLogRepo")

_COLUMNS = (
    "id, live_session_id, pushed_at_ms, card_kind, archive_line_id, commitment_id, sources, detail,"
    " self_triggered, used_at_ms"
)


def _json_list(raw: Any, entry_id: Any) -> List[str]:
    """来源列解成字符串列表；坏数据记一条警告、当没有来源。"""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) and raw else []
    except json.JSONDecodeError as exc:
        logger.warning(f"线索卡记录 {entry_id} 的来源不是合法 JSON（当没有来源）：{exc}")
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _json_dict(raw: Any, entry_id: Any) -> Dict[str, Any]:
    """分数明细列解成字典；坏数据记一条警告、当没有明细。"""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) and raw else {}
    except json.JSONDecodeError as exc:
        logger.warning(f"线索卡记录 {entry_id} 的分数明细不是合法 JSON（当没有明细）：{exc}")
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _entry_of(row: Any) -> CueLogEntry:
    data: Dict[str, Any] = dict(row)
    data["sources"] = _json_list(data.get("sources"), data.get("id"))
    data["detail"] = _json_dict(data.get("detail"), data.get("id"))
    data["self_triggered"] = bool(data.get("self_triggered"))
    return CueLogEntry.model_validate(data)


class MemoryCueLogRepo(BaseRepo):
    """线索卡记录：推出去时写一批，检测到用上时回填，复盘按场读。"""

    async def insert_entries(self, *, entries: Sequence[NewCueLogEntry]) -> int:
        """一批推出去的卡一次写进去，返回写了几行。"""
        rows = [
            (
                int(entry.live_session_id),
                int(entry.pushed_at_ms),
                entry.card_kind,
                entry.archive_line_id,
                entry.commitment_id,
                json.dumps(list(entry.sources), ensure_ascii=False),
                json.dumps(dict(entry.detail), ensure_ascii=False),
                1 if entry.self_triggered else 0,
            )
            for entry in entries
        ]
        if not rows:
            return 0

        def _exec() -> int:
            with self._manager.transaction() as conn:
                conn.executemany(
                    "INSERT INTO memory_cue_log(live_session_id, pushed_at_ms, card_kind, archive_line_id,"
                    " commitment_id, sources, detail, self_triggered) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            return len(rows)

        return await self._run_in_executor(_exec)

    async def mark_used(
        self,
        *,
        live_session_id: int,
        archive_line_ids: Iterable[int] = (),
        commitment_ids: Iterable[int] = (),
        used_at_ms: int,
    ) -> int:
        """本场推过、还没记用上时刻的卡，指向的条目已经被她点名用上：回填用上的时刻，返回回填几行。

        只回填推送早于这一刻的记录：同一场里还没推就被她从简报里点名用上的条目，不算卡片的功劳。
        """
        archive = sorted({int(item) for item in archive_line_ids})
        commitments = sorted({int(item) for item in commitment_ids})
        if not archive and not commitments:
            return 0

        def _exec() -> int:
            updated = 0
            with self._manager.transaction() as conn:
                for column, ids in (("archive_line_id", archive), ("commitment_id", commitments)):
                    if not ids:
                        continue
                    placeholders = ",".join("?" for _ in ids)
                    cur = conn.execute(
                        f"UPDATE memory_cue_log SET used_at_ms=? WHERE live_session_id=? AND used_at_ms IS NULL"  # noqa: S608 常量列名
                        f" AND pushed_at_ms<=? AND {column} IN ({placeholders})",
                        (int(used_at_ms), int(live_session_id), int(used_at_ms), *ids),
                    )
                    updated += int(cur.rowcount or 0)
            return updated

        return await self._run_in_executor(_exec)

    async def list_session_entries(self, *, live_session_id: int) -> List[CueLogEntry]:
        """某一场推过的全部线索卡（按推送时刻先后）。"""

        def _exec() -> List[CueLogEntry]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM memory_cue_log WHERE live_session_id=?"  # noqa: S608 常量列名
                    " ORDER BY pushed_at_ms ASC, id ASC",
                    (int(live_session_id),),
                ).fetchall()
            return [_entry_of(row) for row in rows]

        return await self._run_in_executor(_exec)


__all__ = ["MemoryCueLogRepo"]
