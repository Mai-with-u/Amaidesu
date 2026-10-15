"""ViewerProfileRepo —— 常客名册仓储（viewer_profiles）。

每位常客一行印象文本，身份键 ``(platform, user_id)``。昵称不进本表
（随改名漂移，权威在 ``viewers``），注入与工具查询时经 ``ViewerRepo`` 实时解析。

``last_compressed_at_ms`` 是原料水位：该时刻之前的弹幕与付费记录已融进
印象，收播时只把水位之后的新原料喂给下一次更新。
"""

from __future__ import annotations

import sqlite3
from typing import Any, List, Optional, Tuple

from src.modules.storage.repos._base import BaseRepo
from src.modules.time_utils import now_ms


class ViewerProfileRepo(BaseRepo):
    """viewer_profiles 表的读写面（主播 Agent 收播更新 / Planner 注入 / WebUI 管理）。"""

    async def get_profile_text(self, *, platform: str, user_id: str) -> Optional[str]:
        """取印象文本；无印象返回 None（"有印象才注入"的判定点）。"""

        def _exec() -> Optional[str]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT profile_text FROM viewer_profiles WHERE platform=? AND user_id=?",
                    (platform, user_id),
                ).fetchone()
                return str(row["profile_text"]) if row else None

        return await self._run_in_executor(_exec)

    async def get_profile(self, *, platform: str, user_id: str) -> Optional[sqlite3.Row]:
        """取完整印象行（含水位）；无印象返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return conn.execute(
                    "SELECT platform, user_id, profile_text, last_compressed_at_ms, updated_at_ms"
                    " FROM viewer_profiles WHERE platform=? AND user_id=?",
                    (platform, user_id),
                ).fetchone()

        return await self._run_in_executor(_exec)

    async def upsert_profile(
        self,
        *,
        platform: str,
        user_id: str,
        profile_text: str,
        last_compressed_at_ms: int,
    ) -> bool:
        """写入或更新印象（收播更新写回；水位一并推进）。空文本不写，返回 False。"""
        text = (profile_text or "").strip()
        if not text or not platform or not user_id:
            return False
        ts = now_ms()

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                conn.execute(
                    "INSERT INTO viewer_profiles(platform, user_id, profile_text, last_compressed_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(platform, user_id) DO UPDATE SET"
                    " profile_text=excluded.profile_text,"
                    " last_compressed_at_ms=excluded.last_compressed_at_ms,"
                    " updated_at_ms=excluded.updated_at_ms",
                    (platform, user_id, text, int(last_compressed_at_ms), ts),
                )
                return True

        return await self._run_in_executor(_exec)

    async def list_profiles(
        self,
        *,
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[int, List[sqlite3.Row]]:
        """名册列表（WebUI 管理面）：搜索 + 分页，返回 ``(命中总数, 当页行)``。

        ``search`` 非空时对 profile_text / user_id 做 LIKE 子串匹配。
        """
        clauses: List[str] = []
        params: List[Any] = []
        keyword = (search or "").strip()
        if keyword:
            clauses.append("(profile_text LIKE ? OR user_id LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like])
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        page_limit = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))

        def _exec() -> Tuple[int, List[sqlite3.Row]]:
            with self._manager.transaction() as conn:
                total_row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM viewer_profiles{where_sql}",  # noqa: S608 子句只含占位符
                    tuple(params),
                ).fetchone()
                rows = conn.execute(
                    "SELECT platform, user_id, profile_text, last_compressed_at_ms, updated_at_ms"
                    f" FROM viewer_profiles{where_sql} ORDER BY updated_at_ms DESC LIMIT ? OFFSET ?",  # noqa: S608
                    [*params, page_limit, page_offset],
                ).fetchall()
            total = int(total_row["n"]) if total_row else 0
            return total, list(rows)

        return await self._run_in_executor(_exec)

    async def count_profiles(self) -> int:
        """名册总行数（管理面统计）。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM viewer_profiles").fetchone()
                return int(row["n"]) if row else 0

        return await self._run_in_executor(_exec)

    async def update_profile_text(self, *, platform: str, user_id: str, profile_text: str) -> bool:
        """人工纠正印象文本（WebUI）；行不存在或空文本返回 False。水位不动——纠正版作为旧印象参与下次更新。"""
        text = (profile_text or "").strip()
        if not text:
            return False

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE viewer_profiles SET profile_text=?, updated_at_ms=? WHERE platform=? AND user_id=?",
                    (text, now_ms(), platform, user_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_profile(self, *, platform: str, user_id: str) -> bool:
        """删除印象（WebUI）；行不存在返回 False。删除后该观众回到"无印象"态。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "DELETE FROM viewer_profiles WHERE platform=? AND user_id=?",
                    (platform, user_id),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)


__all__ = ["ViewerProfileRepo"]
