"""GameWorkDocsRepo —— 游戏工作文档仓储（game_work_docs）。

游戏 Agent 的工作笔记（notebook）与待办（todos）按 ``(game, save_id)`` 一存档一行覆盖落库：
进存档时读出，写笔记或写待办时写回，跨进程重启保留；换到别的存档各自独立不串味。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.modules.storage.models.game_work_docs import GameWorkDocRecord
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

__all__ = ["GameWorkDocsRepo"]


def _parse_todos(raw: Any) -> List[Dict[str, str]]:
    """反序列化库里的 todos JSON 文本，异常或非列表时安全回退空列表。"""
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    items: List[Dict[str, str]] = []
    for entry in parsed:
        if isinstance(entry, Mapping):
            items.append(
                {
                    "content": str(entry.get("content", "")),
                    "status": str(entry.get("status", "pending")),
                }
            )
    return items


class GameWorkDocsRepo(BaseRepo):
    """game_work_docs 表的读写入口（游戏 Agent 认出存档时读、文档更新时写）。"""

    async def get_work_docs(self, *, game: str, save_id: str) -> Optional[GameWorkDocRecord]:
        """读取指定游戏与存档的工作文档；尚未落库时返回 ``None``。"""
        if not save_id:
            return None

        def _exec() -> Optional[GameWorkDocRecord]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT save_id, save_name, notebook, todos, updated_session_id, updated_at_ms"
                    " FROM game_work_docs WHERE game=? AND save_id=?",
                    (game, save_id),
                ).fetchone()
                if row is None:
                    return None
                return GameWorkDocRecord(
                    save_id=str(row["save_id"]),
                    save_name=str(row["save_name"] or ""),
                    notebook=str(row["notebook"] or ""),
                    todos=_parse_todos(row["todos"]),
                    updated_session_id=row["updated_session_id"],
                    updated_at_ms=int(row["updated_at_ms"] or 0),
                )

        return await self._run_in_executor(_exec)

    async def save_work_docs(
        self,
        *,
        game: str,
        save_id: str,
        save_name: str = "",
        notebook: str = "",
        todos: Sequence[Mapping[str, Any]] = (),
        updated_session_id: Optional[int] = None,
    ) -> None:
        """按 ``(game, save_id)`` 覆盖写入工作笔记与待办列表。"""
        if not save_id:
            return
        ts = now_ms()
        normalized_todos = [
            {"content": str(item.get("content", "")), "status": str(item.get("status", "pending"))} for item in todos
        ]
        todos_json = json.dumps(normalized_todos, ensure_ascii=False, separators=(",", ":"))

        def _exec() -> None:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO game_work_docs("
                    "game, save_id, save_name, notebook, todos, updated_session_id, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(game, save_id) DO UPDATE SET"
                    " save_name = CASE WHEN excluded.save_name != '' THEN excluded.save_name ELSE game_work_docs.save_name END,"
                    " notebook = excluded.notebook,"
                    " todos = excluded.todos,"
                    " updated_session_id = excluded.updated_session_id,"
                    " updated_at_ms = excluded.updated_at_ms",
                    (game, save_id, save_name, notebook, todos_json, updated_session_id, ts),
                )

        await self._run_in_executor(_exec)

    async def move_world(self, *, game: str, from_save_id: str, to_save_id: str) -> int:
        """旧世界编号名下的工作文档迁移到新存档编号（目标编号已有记录时不覆盖）。"""
        if not from_save_id or not to_save_id or from_save_id == to_save_id:
            return 0

        def _exec() -> int:
            with self._manager.transaction() as conn:
                taken = conn.execute(
                    "SELECT 1 FROM game_work_docs WHERE game=? AND save_id=? LIMIT 1",
                    (game, to_save_id),
                ).fetchone()
                if taken is not None:
                    return 0
                cur = conn.execute(
                    "UPDATE game_work_docs SET save_id=? WHERE game=? AND save_id=?",
                    (to_save_id, game, from_save_id),
                )
                return int(cur.rowcount or 0)

        return await self._run_in_executor(_exec)
