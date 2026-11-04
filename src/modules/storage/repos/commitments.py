"""CommitmentRepo —— 承诺账本仓储（commitments）。

承诺 = 她当众答应过、还没了结的事。行由三条路径写入：

- 每个正式场下播整理时「找新承诺」（带原话与登记场次）；
- 同一次整理里「了结旧承诺」（只在原状态 open 时改成 closed）；
- 运营者在 WebUI 手建、判误记（改成 void）或删除。

台账对模型只开放两种写操作：插入新行、把 open 改成 closed；判误记与删除是运营者的
动作。所以模型改不动运营者处理过的行——了结语句自带 status='open' 条件。
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from src.modules.storage.models.memory import Commitment
from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

# 承诺行读面的列（与 Commitment 字段一一对应）
_COMMITMENT_COLUMNS = (
    "id, what, quote, evidence_chat_id, viewer_id, game, status,"
    " created_session_id, close_chat_id, close_quote, created_at_ms, updated_at_ms"
)


def delete_commitments_by_session(conn: sqlite3.Connection, *, live_session_id: int) -> int:
    """删除某一场登记的全部承诺，返回删除条数（场次改类型、删除场次时用）。

    模块级函数供跨表事务内的调用方复用同一条删除语句；CommitmentRepo 的异步方法
    只是它的事务包装。
    """
    cur = conn.execute("DELETE FROM commitments WHERE created_session_id=?", (int(live_session_id),))
    return int(cur.rowcount or 0)


class CommitmentRepo(BaseRepo):
    """commitments 表的读写面。"""

    async def insert_commitment(
        self,
        *,
        what: str,
        quote: str = "",
        evidence_chat_id: Optional[int] = None,
        viewer_id: Optional[int] = None,
        game: Optional[str] = None,
        created_session_id: Optional[int] = None,
        now_ms_value: Optional[int] = None,
    ) -> Optional[int]:
        """登记一条承诺，返回主键；同一句台词已登记过则返回 None（不重复建）。

        evidence_chat_id 有唯一约束：一句话最多登记成一条承诺，回滚重跑与同场重复整理
        都靠它挡住重复行。没有原话的手建条目该列为空；SQLite 视 NULL 互不相同，
        多条手建行不会互相冲突。
        """
        text = (what or "").strip()
        if not text:
            return None
        ts = int(now_ms_value if now_ms_value is not None else now_ms())

        def _exec() -> Optional[int]:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO commitments(what, quote, evidence_chat_id, viewer_id, game,"
                    " created_session_id, created_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(evidence_chat_id) DO NOTHING",
                    (
                        text,
                        (quote or "").strip(),
                        evidence_chat_id,
                        viewer_id,
                        game,
                        created_session_id,
                        ts,
                        ts,
                    ),
                )
                if cur.rowcount <= 0:
                    return None
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def get_commitment(self, *, commitment_id: int) -> Optional[Commitment]:
        """按主键取一条承诺；不存在返回 None。"""

        def _exec() -> Optional[Commitment]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    f"SELECT {_COMMITMENT_COLUMNS} FROM commitments WHERE id=?",  # noqa: S608 列为代码内常量
                    (int(commitment_id),),
                ).fetchone()
                return Commitment.model_validate(dict(row)) if row else None

        return await self._run_in_executor(_exec)

    async def list_commitments(
        self,
        *,
        statuses: Optional[List[str]] = None,
        viewer_id: Optional[int] = None,
        game: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Commitment]:
        """按状态/对象/游戏筛选承诺，新的在前；没给的筛选项不过滤。"""
        conditions: List[str] = []
        params: List[object] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            conditions.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if viewer_id is not None:
            conditions.append("viewer_id=?")
            params.append(int(viewer_id))
        if game is not None:
            conditions.append("game=?")
            params.append(game)
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([max(1, int(limit)), max(0, int(offset))])

        def _exec() -> List[Commitment]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_COMMITMENT_COLUMNS} FROM commitments {where_clause}"  # noqa: S608 条件由固定片段拼成
                    " ORDER BY id DESC LIMIT ? OFFSET ?",
                    params,
                ).fetchall()
                return [Commitment.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def close_commitment(
        self,
        *,
        commitment_id: int,
        close_chat_id: Optional[int] = None,
        close_quote: Optional[str] = None,
        updated_at_ms: Optional[int] = None,
    ) -> bool:
        """把一条未了结的承诺记成已了结；运营者已处理过的（closed / void）不受影响。

        返回是否真的改到了行：False 说明这条早已不是 open（或不存在），调用方按
        「她说的话不改运营者已定的结论」处理。
        """
        ts = int(updated_at_ms if updated_at_ms is not None else now_ms())

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE commitments SET status='closed', close_chat_id=?, close_quote=?, updated_at_ms=?"
                    " WHERE id=? AND status='open'",
                    (close_chat_id, (close_quote or None), ts, int(commitment_id)),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def void_commitment(self, *, commitment_id: int, updated_at_ms: Optional[int] = None) -> bool:
        """运营者判误记：转 void 留一行墓碑（保住「别再登记回来」的判据）。"""
        ts = int(updated_at_ms if updated_at_ms is not None else now_ms())

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE commitments SET status='void', updated_at_ms=? WHERE id=?",
                    (ts, int(commitment_id)),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_commitment(self, *, commitment_id: int) -> bool:
        """运营者删除一条承诺（彻底清掉，连墓碑也不留）。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM commitments WHERE id=?", (int(commitment_id),))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_by_session(self, *, live_session_id: int) -> int:
        """删除某一场登记的全部承诺，返回删除条数。

        场次类型从正式场改回彩排时用：那一场的话不该进正式的账。重新整理会重新登记。
        """

        def _exec() -> int:
            with self._manager.transaction() as conn:
                return delete_commitments_by_session(conn, live_session_id=int(live_session_id))

        return await self._run_in_executor(_exec)

    async def list_open_before(self, *, before_ms: int, limit: int = 200) -> List[Commitment]:
        """本场开播之前登记的未了结承诺（最老的在前）：下播整理拿它们去核对有没有做完。"""
        params: List[object] = [int(before_ms), max(1, int(limit))]

        def _exec() -> List[Commitment]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT {_COMMITMENT_COLUMNS} FROM commitments"  # noqa: S608 列为代码内常量
                    " WHERE status='open' AND created_at_ms < ? ORDER BY id ASC LIMIT ?",
                    params,
                ).fetchall()
                return [Commitment.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def count_commitments(self, *, statuses: Optional[List[str]] = None) -> int:
        """按状态数承诺条数；不给状态即全部。"""
        params: List[object] = []
        where_clause = ""
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where_clause = f" WHERE status IN ({placeholders})"
            params.extend(statuses)

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM commitments{where_clause}",  # noqa: S608 条件由固定片段拼成
                    params,
                ).fetchone()
                return int(row[0]) if row else 0

        return await self._run_in_executor(_exec)


__all__ = ["CommitmentRepo", "delete_commitments_by_session"]
