"""SocialFactRepo —— 游戏社交事实仓储（``game_social_facts``）。

一行 = 一条"当前游戏环境里人与人怎么相处"的可核对事实。与承诺账本同构：
行级状态、证据可核、人工锁。

写入路径有两条，都经本仓储：

- 场内：主播 Agent 的 ``streamer_social_fact`` 工具（Planner 决策层调用），
  登记一条新事实或用新行取代旧行；
- 下播整理的第四次调用：归纳"她的角色"这类行、补漏、修正场内写歪的行。

存储层的硬约束（都在这里，不靠调用方自觉）：

- **kind 必须是六类之一**；
- **空引文行只允许归纳类两行**（``her_role`` / ``open_question``）——其余四类
  必须带可核对的原文依据；
- **人工锁的行拒改**：``human_locked=1`` 的行既不能被改，也不能被取代
  （运营者改过即定案，整理与工具都不得动，只能删）；
- **取代是链式的**：新行落库时把旧行标成 ``superseded`` 并回填
  ``superseded_by``，旧行保留备查、不再注入。

事实过时用取代表达，不物理删除——历史归场次档案。
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Sequence

from src.modules.storage.models.social_facts import (
    QUOTE_OPTIONAL_KINDS,
    SOCIAL_FACT_KINDS,
    NewSocialFact,
    SocialFact,
)
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

__all__ = ["SocialFactRepo"]

# 读面列（与 SocialFact 字段一一对应）
_SOCIAL_FACT_COLUMNS = (
    "id, game, save_id, save_name, kind, text, quote, evidence_chat_id,"
    " evidence_game_chat_id, status, superseded_by, human_locked,"
    " created_session_id, updated_session_id, created_at_ms, updated_at_ms"
)


class SocialFactRepo(BaseRepo):
    """``game_social_facts`` 表的读写面。"""

    async def insert_fact(
        self,
        *,
        kind: str,
        text: str,
        quote: str = "",
        game: str = "",
        save_id: str = "",
        save_name: str = "",
        evidence_chat_id: Optional[int] = None,
        evidence_game_chat_id: Optional[int] = None,
        supersedes_id: Optional[int] = None,
        human_locked: int = 0,
        created_session_id: Optional[int] = None,
        updated_session_id: Optional[int] = None,
        now_ms_value: Optional[int] = None,
    ) -> Optional[int]:
        """登记一条事实，返回新行号；不满足存储层约束时返回 None。

        ``supersedes_id`` 给定时同时完成取代：旧行标 ``superseded`` 并回填
        ``superseded_by``。旧行是人工锁、状态不是 active、或 kind 与新行
        不同类时，整条登记不落库（返回 None）。
        """
        fact_text = (text or "").strip()
        fact_quote = (quote or "").strip()
        if not fact_text or kind not in SOCIAL_FACT_KINDS:
            return None
        if not fact_quote and kind not in QUOTE_OPTIONAL_KINDS:
            # 非归纳类必须带可核对的原文依据
            return None
        ts = int(now_ms_value if now_ms_value is not None else now_ms())

        def _exec() -> Optional[int]:
            with self._manager.transaction() as conn:
                if supersedes_id is not None:
                    old = conn.execute(
                        "SELECT id, kind, status, human_locked FROM game_social_facts WHERE id=?",
                        (int(supersedes_id),),
                    ).fetchone()
                    if old is None:
                        return None
                    if int(old["human_locked"]) != 0 or str(old["status"]) != "active":
                        # 人工锁或已失效的行不得被取代
                        return None
                    if str(old["kind"]) != kind:
                        # 取代只在同类之间发生（不同类的事实各自独立）
                        return None
                cur = conn.execute(
                    "INSERT INTO game_social_facts(game, save_id, save_name, kind, text, quote,"
                    " evidence_chat_id, evidence_game_chat_id, status, superseded_by, human_locked,"
                    " created_session_id, updated_session_id, created_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?, ?, ?, ?)",
                    (
                        game or "",
                        save_id or "",
                        save_name or "",
                        kind,
                        fact_text,
                        fact_quote,
                        evidence_chat_id,
                        evidence_game_chat_id,
                        int(human_locked),
                        created_session_id,
                        updated_session_id,
                        ts,
                        ts,
                    ),
                )
                new_id = int(cur.lastrowid or 0)
                if supersedes_id is not None:
                    conn.execute(
                        "UPDATE game_social_facts SET status='superseded', superseded_by=?, updated_at_ms=? WHERE id=?",
                        (new_id, ts, int(supersedes_id)),
                    )
                return new_id

        return await self._run_in_executor(_exec)

    async def get_fact(self, *, fact_id: int) -> Optional[SocialFact]:
        """按主键取一条事实；不存在返回 None。"""

        def _exec() -> Optional[SocialFact]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    f"SELECT {_SOCIAL_FACT_COLUMNS} FROM game_social_facts WHERE id=?",  # noqa: S608 列为代码内常量
                    (int(fact_id),),
                ).fetchone()
                return SocialFact.model_validate(dict(row)) if row else None

        return await self._run_in_executor(_exec)

    async def list_active_facts(
        self,
        *,
        game: Optional[str] = None,
        save_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[SocialFact]:
        """现行事实（``status='active'``），新的在前。

        ``save_id`` 给了就只取该存档下的事实；存档为空串的行走"整游戏一本"
        的口径——同一存档下的事实与整游戏通用的事实都算数（不分环境的游戏
        里根本没有存档这一层）。
        """
        conditions: List[str] = ["status='active'"]
        params: List[object] = []
        if game is not None:
            conditions.append("game=?")
            params.append(game)
        if save_id is not None:
            conditions.append("(save_id=? OR save_id='')")
            params.append(save_id)
        params.append(max(1, int(limit)))
        where_clause = " AND ".join(conditions)

        def _exec() -> List[SocialFact]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_SOCIAL_FACT_COLUMNS} FROM game_social_facts WHERE {where_clause}"  # noqa: S608 条件由固定片段拼成
                    " ORDER BY id DESC LIMIT ?",
                    params,
                ).fetchall()
                return [SocialFact.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def count_facts_by_kind(self, *, game: Optional[str] = None) -> Dict[str, int]:
        """现行事实按类计数（防刷屏上限判定与工具回执用）。"""
        conditions: List[str] = ["status='active'"]
        params: List[object] = []
        if game is not None:
            conditions.append("game=?")
            params.append(game)
        where_clause = " AND ".join(conditions)

        def _exec() -> Dict[str, int]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT kind, COUNT(*) FROM game_social_facts WHERE {where_clause} GROUP BY kind",  # noqa: S608
                    params,
                ).fetchall()
            return {str(row[0]): int(row[1]) for row in rows}

        return await self._run_in_executor(_exec)

    async def lock_fact(self, *, fact_id: int, updated_at_ms: Optional[int] = None) -> bool:
        """把一行标成人工锁（运营者改过即锁：之后整理与工具都不得改或取代）。"""
        ts = int(updated_at_ms if updated_at_ms is not None else now_ms())

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE game_social_facts SET human_locked=1, updated_at_ms=? WHERE id=?",
                    (ts, int(fact_id)),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_fact(self, *, fact_id: int) -> bool:
        """彻底删掉一行（运营者删除；人工锁的行也可删——锁只挡改，不挡删）。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM game_social_facts WHERE id=?", (int(fact_id),))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def insert_facts(
        self,
        *,
        facts: Sequence[NewSocialFact],
        now_ms_value: Optional[int] = None,
    ) -> List[int]:
        """批量写入（下播整理用）：逐条走同一套约束，返回落库的行号。

        一条不合法不影响其余（逐条独立判断），不合法的那条不进结果里——
        调用方据此把"没登记成的"写进整理报告。
        """
        written: List[int] = []
        for fact in facts:
            fact_id = await self.insert_fact(
                kind=fact.kind,
                text=fact.text,
                quote=fact.quote,
                game=fact.game,
                save_id=fact.save_id,
                save_name=fact.save_name,
                evidence_chat_id=fact.evidence_chat_id,
                evidence_game_chat_id=fact.evidence_game_chat_id,
                supersedes_id=fact.supersedes_id,
                human_locked=fact.human_locked,
                created_session_id=fact.created_session_id,
                updated_session_id=fact.updated_session_id,
                now_ms_value=now_ms_value,
            )
            if fact_id is not None:
                written.append(fact_id)
        return written

    async def supersede_fact(
        self,
        *,
        fact_id: int,
        superseded_by: int,
        updated_at_ms: Optional[int] = None,
    ) -> bool:
        """把一行标成被取代（旧行保留备查，不再注入）。

        人工锁的行拒改：返回 False 表示没改到。
        """
        ts = int(updated_at_ms if updated_at_ms is not None else now_ms())

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE game_social_facts SET status='superseded', superseded_by=?, updated_at_ms=?"
                    " WHERE id=? AND status='active' AND human_locked=0",
                    (int(superseded_by), ts, int(fact_id)),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)


def delete_social_facts_by_session(conn: sqlite3.Connection, *, live_session_id: int) -> int:
    """删除某一场登记的全部事实，返回删除条数（场次改类型、删除场次时用）。

    模块级函数供跨表事务内的调用方复用同一条删除语句。
    """
    cur = conn.execute("DELETE FROM game_social_facts WHERE created_session_id=?", (int(live_session_id),))
    return int(cur.rowcount or 0)
