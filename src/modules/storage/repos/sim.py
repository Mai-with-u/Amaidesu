"""SimRepo —— 模拟器运行时两表仓储（sim_personas + sim_gifts）。"""

from __future__ import annotations

import sqlite3
import time
from typing import Dict, List, Optional

from src.modules.storage.repos._base import BaseRepo


class SimRepo(BaseRepo):
    """sim_personas / sim_gifts 的 CRUD。"""

    # 可更新字段白名单：动态 SET 拼接前逐一校验，非法键直接拒绝
    # （防列名注入，同 ViewerRepo 的 order_by 白名单思路）
    _PERSONA_UPDATABLE_FIELDS = frozenset(
        {
            "user_nickname",
            "role",
            "personality",
            "speaking_style",
            "fans_medal_level",
            "guard_level",
            "context_window_size",
            "is_active",
            "messages_generated",
        }
    )
    _GIFT_UPDATABLE_FIELDS = frozenset(
        {
            "gift_name",
            "category",
            "weight",
            "data_type",
            "sc_amount_rmb",
        }
    )

    async def count_sim_personas(self) -> int:
        """返回常驻人设总数（含停用行），供启动期种子导入判断空表。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM sim_personas").fetchone()
                return int(row["n"]) if row else 0

        return await self._run_in_executor(_exec)

    async def list_sim_personas(self, *, include_inactive: bool = False) -> List[sqlite3.Row]:
        """列出常驻人设（按昵称排序）；默认排除停用行。"""

        def _exec() -> List[sqlite3.Row]:
            sql = "SELECT * FROM sim_personas"
            if not include_inactive:
                sql += " WHERE is_active=1"
            sql += " ORDER BY user_nickname ASC"
            with self._manager.transaction() as conn:
                return list(conn.execute(sql).fetchall())

        return await self._run_in_executor(_exec)

    async def insert_sim_persona(
        self,
        *,
        user_id: str,
        user_nickname: str,
        role: str,
        personality: str,
        speaking_style: str,
        fans_medal_level: int = 0,
        guard_level: int = 0,
        context_window_size: Optional[int] = None,
        is_active: bool = True,
        messages_generated: int = 0,
    ) -> int:
        """插入一条常驻人设，返回 lastrowid；``user_id`` 冲突抛 IntegrityError。"""
        now_ms = int(time.time() * 1000)

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO sim_personas("
                    "user_id, user_nickname, role, personality, speaking_style,"
                    " fans_medal_level, guard_level, context_window_size,"
                    " is_active, messages_generated, created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        user_nickname,
                        role,
                        personality,
                        speaking_style,
                        fans_medal_level,
                        guard_level,
                        context_window_size,
                        1 if is_active else 0,
                        messages_generated,
                        now_ms,
                        now_ms,
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def update_sim_persona(self, *, user_id: str, fields: Dict[str, object]) -> bool:
        """按字段更新常驻人设（白名单校验键名），自动维护 ``updated_at_ms``。

        Returns:
            True 更新成功；False 人设不存在或无可更新字段。
        """
        if not fields:
            return False
        unknown = set(fields) - self._PERSONA_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"update_sim_persona 非法字段: {sorted(unknown)}")
        set_sql = ", ".join(f"{key}=?" for key in fields)
        params = list(fields.values()) + [int(time.time() * 1000), user_id]

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    f"UPDATE sim_personas SET {set_sql}, updated_at_ms=? WHERE user_id=?",  # noqa: S608
                    params,
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_sim_persona(self, *, user_id: str) -> bool:
        """删除常驻人设行。

        Returns:
            True 删除成功；False 人设不存在。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM sim_personas WHERE user_id=?", (user_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def count_sim_gifts(self) -> int:
        """返回礼物目录条目总数，供启动期种子导入判断空表。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM sim_gifts").fetchone()
                return int(row["n"]) if row else 0

        return await self._run_in_executor(_exec)

    async def list_sim_gifts(self) -> List[sqlite3.Row]:
        """列出礼物目录（按 id 排序，保持插入顺序）。"""

        def _exec() -> List[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return list(conn.execute("SELECT * FROM sim_gifts ORDER BY id ASC").fetchall())

        return await self._run_in_executor(_exec)

    async def insert_sim_gift(
        self,
        *,
        gift_id: str,
        gift_name: str,
        category: str,
        weight: int = 1,
        data_type: str,
        sc_amount_rmb: Optional[int] = None,
    ) -> int:
        """插入一条礼物目录条目，返回 lastrowid；``gift_id`` 冲突抛 IntegrityError。"""
        now_ms = int(time.time() * 1000)

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO sim_gifts("
                    "gift_id, gift_name, category, weight, data_type, sc_amount_rmb,"
                    " created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (gift_id, gift_name, category, weight, data_type, sc_amount_rmb, now_ms, now_ms),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def update_sim_gift(self, *, gift_id: str, fields: Dict[str, object]) -> bool:
        """按字段更新礼物目录条目（白名单校验键名），自动维护 ``updated_at_ms``。

        Returns:
            True 更新成功；False 礼物不存在或无可更新字段。
        """
        if not fields:
            return False
        unknown = set(fields) - self._GIFT_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"update_sim_gift 非法字段: {sorted(unknown)}")
        set_sql = ", ".join(f"{key}=?" for key in fields)
        params = list(fields.values()) + [int(time.time() * 1000), gift_id]

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    f"UPDATE sim_gifts SET {set_sql}, updated_at_ms=? WHERE gift_id=?",  # noqa: S608
                    params,
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_sim_gift(self, *, gift_id: str) -> bool:
        """删除礼物目录条目。

        Returns:
            True 删除成功；False 礼物不存在。
        """

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM sim_gifts WHERE gift_id=?", (gift_id,))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)


__all__ = ["SimRepo"]
