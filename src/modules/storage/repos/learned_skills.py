"""LearnedSkillRepo —— 游戏 Agent 自建技能卡仓储（agent_learned_skills）。

按 ``(game, scope, save_id, name)`` 读写游戏 Agent 沉淀的可复用技能卡：
- ``scope="world"``：绑定具体存档（``save_id`` 非空），存本服专属设施与流程；
- ``scope="global"``：跨存档通用（``save_id`` 存空串），存通用玩法与模组操作经验。
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.modules.storage.models.learned_skills import LearnedSkillRecord, LearnedSkillScope
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

__all__ = ["LearnedSkillRepo"]


def _row_to_record(row: Any) -> LearnedSkillRecord:
    """将数据库查询行转换为强类型 ``LearnedSkillRecord``。"""
    scope_raw = str(row["scope"])
    scope: LearnedSkillScope = "global" if scope_raw == "global" else "world"
    return LearnedSkillRecord(
        id=int(row["id"]),
        game=str(row["game"]),
        save_id=str(row["save_id"] or ""),
        scope=scope,
        name=str(row["name"]),
        trigger=str(row["trigger"] or ""),
        content=str(row["content"]),
        updated_at_ms=int(row["updated_at_ms"] or 0),
    )


class LearnedSkillRepo(BaseRepo):
    """``agent_learned_skills`` 表的按域读写仓储。"""

    async def list_skills(
        self,
        *,
        game: str,
        save_id: str = "",
        scope: Optional[LearnedSkillScope] = None,
    ) -> List[LearnedSkillRecord]:
        """按游戏与可选作用域列出当前可见的自建技能卡（按 scope、name 排序）。

        当 ``scope`` 为 None 时，同时返回该游戏的全部 ``global`` 技能及当前 ``save_id`` 对应的
        ``world`` 技能（未认出存档即 ``save_id`` 为空时仅返回 ``global`` 技能）。
        """
        clean_game = (game or "").strip()
        clean_save = (save_id or "").strip()
        if not clean_game:
            return []

        def _exec() -> List[LearnedSkillRecord]:
            with self._manager.transaction() as conn:
                if scope == "global":
                    rows = conn.execute(
                        "SELECT id, game, save_id, scope, name, trigger, content, updated_at_ms"
                        " FROM agent_learned_skills"
                        " WHERE game=? AND scope='global' AND save_id=''"
                        " ORDER BY name",
                        (clean_game,),
                    ).fetchall()
                elif scope == "world":
                    if not clean_save:
                        return []
                    rows = conn.execute(
                        "SELECT id, game, save_id, scope, name, trigger, content, updated_at_ms"
                        " FROM agent_learned_skills"
                        " WHERE game=? AND scope='world' AND save_id=?"
                        " ORDER BY name",
                        (clean_game, clean_save),
                    ).fetchall()
                elif clean_save:
                    rows = conn.execute(
                        "SELECT id, game, save_id, scope, name, trigger, content, updated_at_ms"
                        " FROM agent_learned_skills"
                        " WHERE game=? AND ((scope='world' AND save_id=?) OR (scope='global' AND save_id=''))"
                        " ORDER BY scope DESC, name",
                        (clean_game, clean_save),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, game, save_id, scope, name, trigger, content, updated_at_ms"
                        " FROM agent_learned_skills"
                        " WHERE game=? AND scope='global' AND save_id=''"
                        " ORDER BY name",
                        (clean_game,),
                    ).fetchall()
                return [_row_to_record(row) for row in rows]

        return await self._run_in_executor(_exec)

    async def get_skill(
        self,
        *,
        game: str,
        name: str,
        save_id: str = "",
        scope: Optional[LearnedSkillScope] = None,
    ) -> Optional[LearnedSkillRecord]:
        """按名称读取单条自建技能卡；未指定作用域时优先查本存档 ``world``，未命中再回退 ``global``。"""
        clean_game = (game or "").strip()
        clean_name = (name or "").strip()
        clean_save = (save_id or "").strip()
        if not clean_game or not clean_name:
            return None

        def _exec() -> Optional[LearnedSkillRecord]:
            with self._manager.transaction() as conn:
                if scope == "global":
                    row = conn.execute(
                        "SELECT id, game, save_id, scope, name, trigger, content, updated_at_ms"
                        " FROM agent_learned_skills"
                        " WHERE game=? AND scope='global' AND save_id='' AND name=?",
                        (clean_game, clean_name),
                    ).fetchone()
                    return _row_to_record(row) if row is not None else None
                if scope == "world":
                    if not clean_save:
                        return None
                    row = conn.execute(
                        "SELECT id, game, save_id, scope, name, trigger, content, updated_at_ms"
                        " FROM agent_learned_skills"
                        " WHERE game=? AND scope='world' AND save_id=? AND name=?",
                        (clean_game, clean_save, clean_name),
                    ).fetchone()
                    return _row_to_record(row) if row is not None else None
                # 未指定作用域：先查当前存档定制技能，再查跨存档通用技能
                if clean_save:
                    row = conn.execute(
                        "SELECT id, game, save_id, scope, name, trigger, content, updated_at_ms"
                        " FROM agent_learned_skills"
                        " WHERE game=? AND scope='world' AND save_id=? AND name=?",
                        (clean_game, clean_save, clean_name),
                    ).fetchone()
                    if row is not None:
                        return _row_to_record(row)
                row = conn.execute(
                    "SELECT id, game, save_id, scope, name, trigger, content, updated_at_ms"
                    " FROM agent_learned_skills"
                    " WHERE game=? AND scope='global' AND save_id='' AND name=?",
                    (clean_game, clean_name),
                ).fetchone()
                return _row_to_record(row) if row is not None else None

        return await self._run_in_executor(_exec)

    async def upsert_skill(
        self,
        *,
        game: str,
        scope: LearnedSkillScope,
        save_id: str,
        name: str,
        trigger: str,
        content: str,
        updated_at_ms: Optional[int] = None,
    ) -> LearnedSkillRecord:
        """新建或覆盖更新一条自建技能卡；``global`` 作用域强制存空 ``save_id``。"""
        clean_game = (game or "").strip()
        clean_name = (name or "").strip()
        clean_trigger = (trigger or "").strip()
        clean_content = (content or "").strip()
        if not clean_game:
            raise ValueError("game 不能为空")
        if not clean_name:
            raise ValueError("name 不能为空")
        if not clean_content:
            raise ValueError("content 不能为空")
        if scope not in ("world", "global"):
            raise ValueError(f"不支持的 scope: {scope!r}")
        effective_save = "" if scope == "global" else (save_id or "").strip()
        if scope == "world" and not effective_save:
            raise ValueError("world 作用域技能必须提供非空 save_id")
        ts = int(updated_at_ms) if updated_at_ms is not None else now_ms()

        def _exec() -> LearnedSkillRecord:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO agent_learned_skills(game, save_id, scope, name, trigger, content, updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(game, scope, save_id, name) DO UPDATE SET"
                    " trigger = excluded.trigger,"
                    " content = excluded.content,"
                    " updated_at_ms = excluded.updated_at_ms",
                    (clean_game, effective_save, scope, clean_name, clean_trigger, clean_content, ts),
                )
                row = conn.execute(
                    "SELECT id, game, save_id, scope, name, trigger, content, updated_at_ms"
                    " FROM agent_learned_skills"
                    " WHERE game=? AND scope=? AND save_id=? AND name=?",
                    (clean_game, scope, effective_save, clean_name),
                ).fetchone()
                assert row is not None  # 刚在同一事务内完成写入，必然存在
                return _row_to_record(row)

        return await self._run_in_executor(_exec)

    async def delete_skill(
        self,
        *,
        game: str,
        name: str,
        save_id: str = "",
        scope: Optional[LearnedSkillScope] = None,
    ) -> bool:
        """按名称删除自建技能卡，返回是否实际删除了记录。"""
        clean_game = (game or "").strip()
        clean_name = (name or "").strip()
        clean_save = (save_id or "").strip()
        if not clean_game or not clean_name:
            return False

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                if scope == "global":
                    cur = conn.execute(
                        "DELETE FROM agent_learned_skills WHERE game=? AND scope='global' AND save_id='' AND name=?",
                        (clean_game, clean_name),
                    )
                    return int(cur.rowcount or 0) > 0
                if scope == "world":
                    if not clean_save:
                        return False
                    cur = conn.execute(
                        "DELETE FROM agent_learned_skills WHERE game=? AND scope='world' AND save_id=? AND name=?",
                        (clean_game, clean_save, clean_name),
                    )
                    return int(cur.rowcount or 0) > 0
                if clean_save:
                    cur = conn.execute(
                        "DELETE FROM agent_learned_skills WHERE game=? AND scope='world' AND save_id=? AND name=?",
                        (clean_game, clean_save, clean_name),
                    )
                    if int(cur.rowcount or 0) > 0:
                        return True
                cur = conn.execute(
                    "DELETE FROM agent_learned_skills WHERE game=? AND scope='global' AND save_id='' AND name=?",
                    (clean_game, clean_name),
                )
                return int(cur.rowcount or 0) > 0

        return await self._run_in_executor(_exec)

    async def count_skills(
        self,
        *,
        game: str,
        scope: LearnedSkillScope,
        save_id: str = "",
    ) -> int:
        """统计指定游戏与作用域下已存技能卡条数（供单域上限校验）。"""
        clean_game = (game or "").strip()
        if not clean_game:
            return 0
        effective_save = "" if scope == "global" else (save_id or "").strip()
        if scope == "world" and not effective_save:
            return 0

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT COUNT(1) AS cnt FROM agent_learned_skills WHERE game=? AND scope=? AND save_id=?",
                    (clean_game, scope, effective_save),
                ).fetchone()
                return int(row["cnt"]) if row is not None else 0

        return await self._run_in_executor(_exec)
