"""TalkCardRepo —— 话题卡（talk_cards）的读写。

运营在编排台增删改卡、批量导入、重置已用状态；主播在杂谈冷场时按场次挑未用卡，
选用后回写"本场已用"。卡片跨场次长期存在，场次删除不影响卡片。
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Sequence

from src.modules.logging import get_logger
from src.modules.storage.models.talk_card import NewTalkCard, TalkCard, TalkCardUpdate
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

logger = get_logger("TalkCardRepo")

_COLUMNS = (
    "id, kind, title, body, asker, tags_json, enabled, used_in_session_id, used_at_ms, created_at_ms, updated_at_ms"
)


def _parse_tags(raw: Any, card_id: Any) -> List[str]:
    """标签列 JSON → 列表；脏行记告警并按无标签处理，不让一张坏卡拖垮整个卡池。"""
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError) as exc:
        logger.warning(f"话题卡标签 JSON 解析失败，按无标签处理: id={card_id}", exc=exc)
        return []
    if not isinstance(parsed, list):
        logger.warning(f"话题卡标签不是列表，按无标签处理: id={card_id} raw={raw!r}")
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _card_of(row: Any) -> TalkCard:
    data = dict(row)
    return TalkCard(
        id=int(data["id"]),
        kind=data["kind"],
        title=str(data["title"]),
        body=str(data["body"] or ""),
        asker=str(data["asker"] or ""),
        tags=_parse_tags(data.get("tags_json"), data["id"]),
        enabled=bool(data["enabled"]),
        used_in_session_id=data["used_in_session_id"],
        used_at_ms=data["used_at_ms"],
        created_at_ms=int(data["created_at_ms"]),
        updated_at_ms=int(data["updated_at_ms"]),
    )


class TalkCardRepo(BaseRepo):
    """话题卡仓储：编排台增删改与批量导入、主播按场挑卡与标记已用。"""

    async def add_cards(self, cards: Sequence[NewTalkCard]) -> List[TalkCard]:
        """新建一批卡（单张手建与批量导入共用），返回落库后的完整行（与入参同序）。"""

        def _exec() -> List[TalkCard]:
            now = now_ms()
            ids: List[int] = []
            with self._manager.transaction() as conn:
                for card in cards:
                    cur = conn.execute(
                        "INSERT INTO talk_cards(kind, title, body, asker, tags_json, enabled,"
                        " created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            card.kind,
                            card.title,
                            card.body,
                            card.asker,
                            json.dumps(card.tags, ensure_ascii=False),
                            1 if card.enabled else 0,
                            now,
                            now,
                        ),
                    )
                    ids.append(int(cur.lastrowid or 0))
                if not ids:
                    return []
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(f"SELECT {_COLUMNS} FROM talk_cards WHERE id IN ({placeholders})", ids).fetchall()
            by_id = {int(row["id"]): _card_of(row) for row in rows}
            return [by_id[i] for i in ids if i in by_id]

        return await self._run_in_executor(_exec)

    async def list_cards(self, *, kind: Optional[str] = None, enabled_only: bool = False) -> List[TalkCard]:
        """列出卡片（可按卡种与是否启用过滤），按创建先后排序：先投递的问题先被看到。"""
        clauses: List[str] = []
        params: List[Any] = []
        if kind is not None:
            clauses.append("kind=?")
            params.append(kind)
        if enabled_only:
            clauses.append("enabled=1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        def _exec() -> List[TalkCard]:
            with self._manager.transaction() as conn:
                rows = conn.execute(f"SELECT {_COLUMNS} FROM talk_cards{where} ORDER BY id", params).fetchall()
            return [_card_of(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def get_card(self, card_id: int) -> Optional[TalkCard]:
        """按 id 取一张卡；不存在返回 None。"""

        def _exec() -> Optional[TalkCard]:
            with self._manager.transaction() as conn:
                row = conn.execute(f"SELECT {_COLUMNS} FROM talk_cards WHERE id=?", (int(card_id),)).fetchone()
            return _card_of(row) if row is not None else None

        return await self._run_in_executor(_exec)

    async def update_card(self, card_id: int, patch: TalkCardUpdate) -> Optional[TalkCard]:
        """部分更新一张卡（只改显式给出的字段），返回更新后的行；卡不存在返回 None。"""
        fields = patch.model_dump(exclude_none=True)
        assignments: List[str] = []
        params: List[Any] = []
        for name, value in fields.items():
            if name == "tags":
                assignments.append("tags_json=?")
                params.append(json.dumps(value, ensure_ascii=False))
            elif name == "enabled":
                assignments.append("enabled=?")
                params.append(1 if value else 0)
            else:
                assignments.append(f"{name}=?")
                params.append(value)

        def _exec() -> Optional[TalkCard]:
            with self._manager.transaction() as conn:
                if assignments:
                    conn.execute(
                        f"UPDATE talk_cards SET {', '.join(assignments)}, updated_at_ms=? WHERE id=?",
                        (*params, now_ms(), int(card_id)),
                    )
                row = conn.execute(f"SELECT {_COLUMNS} FROM talk_cards WHERE id=?", (int(card_id),)).fetchone()
            return _card_of(row) if row is not None else None

        return await self._run_in_executor(_exec)

    async def delete_card(self, card_id: int) -> bool:
        """删一张卡，返回是否真删到了行。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM talk_cards WHERE id=?", (int(card_id),))
                return int(cur.rowcount or 0) > 0

        return await self._run_in_executor(_exec)

    async def mark_used(self, card_id: int, *, session_id: Optional[int], at_ms: int) -> bool:
        """把一张卡标为"本场已用"（主播选用它开话头时调用），返回是否找到该卡。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE talk_cards SET used_in_session_id=?, used_at_ms=? WHERE id=?",
                    (session_id, int(at_ms), int(card_id)),
                )
                return int(cur.rowcount or 0) > 0

        return await self._run_in_executor(_exec)

    async def reset_used(self, *, card_ids: Optional[Sequence[int]] = None) -> int:
        """清空已用标记（不给 id = 全部卡），返回被重置的行数；运营彩排后一键复原用。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                if card_ids is None:
                    cur = conn.execute(
                        "UPDATE talk_cards SET used_in_session_id=NULL, used_at_ms=NULL WHERE used_at_ms IS NOT NULL"
                    )
                else:
                    ids = [int(i) for i in card_ids]
                    if not ids:
                        return 0
                    placeholders = ",".join("?" for _ in ids)
                    cur = conn.execute(
                        f"UPDATE talk_cards SET used_in_session_id=NULL, used_at_ms=NULL WHERE id IN ({placeholders})",
                        ids,
                    )
                return int(cur.rowcount or 0)

        return await self._run_in_executor(_exec)


__all__ = ["TalkCardRepo"]
