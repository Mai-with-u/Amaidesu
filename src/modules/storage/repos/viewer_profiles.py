"""ViewerProfileRepo —— 观众画像仓储（viewer_profiles）。

每位常客一行印象文本，以内部观众 ID（``viewer_id``）为键：同一位观众换了
数据源、被关联上新的外部 ID 后，画像仍是同一份。昵称不进本表（随改名漂移，
权威在 ``viewers``），注入与工具查询时经 ``ViewerRepo`` 实时解析。

``last_compressed_at_ms`` 是已整理到的时间点：该时刻之前的弹幕与付费记录
已融进印象，收播时只把这之后的新话喂给下一次更新。
"""

from __future__ import annotations

import sqlite3
from typing import Any, List, Optional, Tuple

from src.modules.storage.repos._base import BaseRepo
from src.modules.utils.time_utils import now_ms

# 列表行带上观众的平台与当前昵称，WebUI 不必再逐个反查
_PROFILE_COLUMNS = (
    "p.viewer_id AS viewer_id, v.platform AS platform, v.user_name AS user_name,"
    " p.profile_text AS profile_text, p.last_compressed_at_ms AS last_compressed_at_ms,"
    " p.updated_at_ms AS updated_at_ms"
)
_PROFILE_FROM = " FROM viewer_profiles p JOIN viewers v ON v.id = p.viewer_id"


class ViewerProfileRepo(BaseRepo):
    """viewer_profiles 表的读写入口（主播 Agent 收播更新 / Planner 注入 / WebUI 管理）。"""

    async def get_profile_text(self, *, viewer_id: int) -> Optional[str]:
        """取印象文本；无印象返回 None（"有印象才注入"的判定点）。"""

        def _exec() -> Optional[str]:
            with self._manager.transaction() as conn:
                row = conn.execute(
                    "SELECT profile_text FROM viewer_profiles WHERE viewer_id=?", (int(viewer_id),)
                ).fetchone()
                return str(row["profile_text"]) if row else None

        return await self._run_in_executor(_exec)

    async def get_profile(self, *, viewer_id: int) -> Optional[sqlite3.Row]:
        """取完整印象行（含已整理时刻）；无印象返回 None。"""

        def _exec() -> Optional[sqlite3.Row]:
            with self._manager.transaction() as conn:
                return conn.execute(
                    f"SELECT {_PROFILE_COLUMNS}{_PROFILE_FROM} WHERE p.viewer_id=?",  # noqa: S608 常量片段
                    (int(viewer_id),),
                ).fetchone()

        return await self._run_in_executor(_exec)

    async def upsert_profile(self, *, viewer_id: int, profile_text: str, last_compressed_at_ms: int) -> bool:
        """写入或更新印象（收播更新写回；已整理时刻一并推进）。

        空文本、或这位观众不存在（画像只给已建档的观众）时不写，返回 False。
        """
        text = (profile_text or "").strip()
        if not text:
            return False
        ts = now_ms()

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                if conn.execute("SELECT 1 FROM viewers WHERE id=?", (int(viewer_id),)).fetchone() is None:
                    return False
                conn.execute(
                    "INSERT INTO viewer_profiles(viewer_id, profile_text, last_compressed_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(viewer_id) DO UPDATE SET"
                    " profile_text=excluded.profile_text,"
                    " last_compressed_at_ms=excluded.last_compressed_at_ms,"
                    " updated_at_ms=excluded.updated_at_ms",
                    (int(viewer_id), text, int(last_compressed_at_ms), ts),
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
        """画像列表（WebUI 管理接口）：搜索 + 分页，返回 ``(命中总数, 当页行)``。

        ``search`` 非空时对印象文本与观众当前昵称做 LIKE 子串匹配。
        """
        clauses: List[str] = []
        params: List[Any] = []
        keyword = (search or "").strip()
        if keyword:
            clauses.append("(p.profile_text LIKE ? OR v.user_name LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like])
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        page_limit = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))

        def _exec() -> Tuple[int, List[sqlite3.Row]]:
            with self._manager.transaction() as conn:
                total_row = conn.execute(
                    f"SELECT COUNT(*) AS n{_PROFILE_FROM}{where_sql}",  # noqa: S608 子句只含占位符
                    tuple(params),
                ).fetchone()
                rows = conn.execute(
                    f"SELECT {_PROFILE_COLUMNS}{_PROFILE_FROM}{where_sql}"  # noqa: S608
                    " ORDER BY p.updated_at_ms DESC LIMIT ? OFFSET ?",
                    [*params, page_limit, page_offset],
                ).fetchall()
            total = int(total_row["n"]) if total_row else 0
            return total, list(rows)

        return await self._run_in_executor(_exec)

    async def count_profiles(self) -> int:
        """画像总行数（管理接口统计）。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM viewer_profiles").fetchone()
                return int(row["n"]) if row else 0

        return await self._run_in_executor(_exec)

    async def update_profile_text(self, *, viewer_id: int, profile_text: str) -> bool:
        """人工纠正印象文本（WebUI）；行不存在或空文本返回 False。已整理时刻不动——纠正版作为旧印象参与下次更新。"""
        text = (profile_text or "").strip()
        if not text:
            return False

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "UPDATE viewer_profiles SET profile_text=?, updated_at_ms=? WHERE viewer_id=?",
                    (text, now_ms(), int(viewer_id)),
                )
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)

    async def delete_profile(self, *, viewer_id: int) -> bool:
        """删除印象（WebUI）；行不存在返回 False。删除后该观众回到"无印象"态。"""

        def _exec() -> bool:
            with self._manager.transaction() as conn:
                cur = conn.execute("DELETE FROM viewer_profiles WHERE viewer_id=?", (int(viewer_id),))
                return cur.rowcount > 0

        return await self._run_in_executor(_exec)


__all__ = ["ViewerProfileRepo"]
