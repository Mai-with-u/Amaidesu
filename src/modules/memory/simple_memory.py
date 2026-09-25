"""
SimpleMemory —— 观众事实与画像读写服务

## 定位
承载 ``viewer_facts``（事实原料）与 ``viewer_profiles``（画像文本）两张
业务表的读写面。记忆模块保持通用记忆定位——画像是记忆的一种能力，
将来可接外部记忆系统；本类不承担提取/压缩的编排（那归后台循环），
只做存储读写与查询。

## 接口面
- **事实面**：``add_viewer_fact``（写入，自带同观众同文本去重）/
  ``list_viewer_facts``（按人查）/ ``search_viewer_facts``（关键词召回，
  query_memory 工具的数据面）/ ``delete_viewer_fact``
- **画像面**：``get_viewer_profile``（planner 注入与工具查询）/
  ``upsert_viewer_profile``（增量压缩写回）/ ``list_viewer_profiles``（WebUI
  管理面）/ ``delete_viewer_profile`` / ``update_viewer_profile``（人工纠正）
- **画像生成输入**：``list_profile_candidates``（水位后有新事实且互动量达
  门槛的观众）/ ``list_facts_since``（水位后的新事实）

## 存储说明
两张表均为**业务表**（统一纳管，无私有表机制——``_`` 前缀伪隔离已废除）；
DDL 权威在 ``storage/schema.py``，随 ``SQLiteDatabase.initialize()`` 统一建表。
``initialize()`` 只做表自检，不带 DDL——建表职责单一归 schema.py，避免两处
DDL 漂移。

## 身份键
所有读写以 ``(platform, user_id)`` 复合键定位；昵称不进本模块（权威在
viewers 表，需要时经 ``ViewerRepo.get_viewer_id_by_name`` 反查）。

## 时间单位
- Amaidesu 内部全毫秒，本模块零转换
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, List, Optional, Tuple

from src.modules.logging import get_logger
from src.modules.memory.models import ViewerFact, ViewerProfile, ViewerProfileSummary
from src.modules.storage.database import SQLiteDatabase
from src.modules.time_utils import now_ms

logger = get_logger("SimpleMemory")


class SimpleMemory:
    """观众事实与画像的读写服务（SQLite 持久化）。"""

    def __init__(self, store: SQLiteDatabase) -> None:
        self._store = store

    async def initialize(self) -> None:
        """自检业务表已就位（DDL 由 SQLiteDatabase.initialize() 按 schema.py 统一建）。"""
        for table in ("viewer_facts", "viewer_profiles"):
            if not await self._store.table_exists(table):
                raise RuntimeError(
                    f"SimpleMemory 表 {table} 不存在：请先执行 SQLiteDatabase.initialize()（建表 DDL 权威在 storage/schema.py）"
                )
        logger.debug("SimpleMemory 表自检通过（viewer_facts / viewer_profiles）")

    # -------------------- 事实面 --------------------

    async def add_viewer_fact(
        self,
        *,
        platform: str,
        user_id: str,
        fact_text: str,
        source_message_id: str = "",
        created_at_ms: int = 0,
    ) -> bool:
        """写入一条观众事实；同观众同文本已存在时跳过（去重），返回是否落库。

        提取侧容错的最后一道防线：LLM 重复输出、跨批重复事实在此挡下。
        """
        # 原话支持的完整事实用于后续召回与去重，尾部条件不能在落库时丢失。
        text = (fact_text or "").strip()
        if not text or not platform or not user_id:
            return False
        ts = int(created_at_ms or now_ms())

        def _exec() -> bool:
            with self._manager_tx() as conn:
                dup = conn.execute(
                    "SELECT 1 FROM viewer_facts WHERE platform=? AND user_id=? AND fact_text=? LIMIT 1",
                    (platform, user_id, text),
                ).fetchone()
                if dup:
                    return False
                conn.execute(
                    "INSERT INTO viewer_facts(platform, user_id, fact_text, source_message_id, created_at_ms)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (platform, user_id, text, source_message_id, ts),
                )
                return True

        return await self._store_run(_exec)

    async def list_viewer_facts(
        self,
        *,
        platform: str,
        user_id: str,
        limit: int = 50,
    ) -> List[ViewerFact]:
        """按人列事实（时间倒序，最新在前）。"""

        def _exec() -> List[ViewerFact]:
            with self._manager_tx() as conn:
                rows = conn.execute(
                    "SELECT id, platform, user_id, fact_text, source_message_id, created_at_ms"
                    " FROM viewer_facts WHERE platform=? AND user_id=?"
                    " ORDER BY created_at_ms DESC, id DESC LIMIT ?",
                    (platform, user_id, max(1, min(int(limit), 500))),
                ).fetchall()
            return [
                ViewerFact(
                    fact_id=int(row["id"]),
                    platform=str(row["platform"]),
                    user_id=str(row["user_id"]),
                    fact_text=str(row["fact_text"]),
                    source_message_id=str(row["source_message_id"] or ""),
                    created_at_ms=int(row["created_at_ms"]),
                )
                for row in rows
            ]

        return await self._store_run(_exec)

    async def search_viewer_facts(self, *, query: str, top_k: int = 5) -> List[ViewerFact]:
        """关键词召回事实（LIKE 子串语义），供 query_memory 工具。"""
        keyword = (query or "").strip()
        if not keyword:
            return []

        def _exec() -> List[ViewerFact]:
            with self._manager_tx() as conn:
                rows = conn.execute(
                    "SELECT id, platform, user_id, fact_text, source_message_id, created_at_ms"
                    " FROM viewer_facts WHERE fact_text LIKE ?"
                    " ORDER BY created_at_ms DESC LIMIT ?",
                    (f"%{keyword}%", max(1, min(int(top_k), 20))),
                ).fetchall()
            return [
                ViewerFact(
                    fact_id=int(row["id"]),
                    platform=str(row["platform"]),
                    user_id=str(row["user_id"]),
                    fact_text=str(row["fact_text"]),
                    source_message_id=str(row["source_message_id"] or ""),
                    created_at_ms=int(row["created_at_ms"]),
                )
                for row in rows
            ]

        return await self._store_run(_exec)

    async def list_facts_since(self, *, platform: str, user_id: str, since_ms: int) -> List[ViewerFact]:
        """取水位之后的新事实（画像增量生成的原料窗口，时间正序）。"""

        def _exec() -> List[ViewerFact]:
            with self._manager_tx() as conn:
                rows = conn.execute(
                    "SELECT id, platform, user_id, fact_text, source_message_id, created_at_ms"
                    " FROM viewer_facts WHERE platform=? AND user_id=? AND created_at_ms>?"
                    " ORDER BY created_at_ms ASC LIMIT 200",
                    (platform, user_id, int(since_ms)),
                ).fetchall()
            return [
                ViewerFact(
                    fact_id=int(row["id"]),
                    platform=str(row["platform"]),
                    user_id=str(row["user_id"]),
                    fact_text=str(row["fact_text"]),
                    source_message_id=str(row["source_message_id"] or ""),
                    created_at_ms=int(row["created_at_ms"]),
                )
                for row in rows
            ]

        return await self._store_run(_exec)

    async def count_viewer_facts(self) -> int:
        """事实条目总数（管理面统计用）。"""

        def _exec() -> int:
            with self._manager_tx() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM viewer_facts").fetchone()
                return int(row["n"]) if row else 0

        return await self._store_run(_exec)

    async def delete_viewer_fact(self, *, fact_id: int) -> bool:
        """删除单条事实（WebUI 人工清理）；id 不存在返回 False。"""

        def _exec() -> bool:
            with self._manager_tx() as conn:
                cur = conn.execute("DELETE FROM viewer_facts WHERE id=?", (int(fact_id),))
                return cur.rowcount > 0

        return await self._store_run(_exec)

    # -------------------- 画像面 --------------------

    async def get_viewer_profile(self, *, platform: str, user_id: str) -> Optional[str]:
        """取画像文本；无画像返回 None（"有画像才注入"的判定点）。"""

        def _exec() -> Optional[str]:
            with self._manager_tx() as conn:
                row = conn.execute(
                    "SELECT profile_text FROM viewer_profiles WHERE platform=? AND user_id=?",
                    (platform, user_id),
                ).fetchone()
                return str(row["profile_text"]) if row else None

        return await self._store_run(_exec)

    async def get_viewer_profile_with_watermark(self, *, platform: str, user_id: str) -> Optional[ViewerProfile]:
        """取完整画像行（含水位）；无画像返回 None。"""

        def _exec() -> Optional[ViewerProfile]:
            with self._manager_tx() as conn:
                row = conn.execute(
                    "SELECT platform, user_id, profile_text, last_compressed_at_ms, updated_at_ms"
                    " FROM viewer_profiles WHERE platform=? AND user_id=?",
                    (platform, user_id),
                ).fetchone()
            if row is None:
                return None
            return ViewerProfile(
                platform=str(row["platform"]),
                user_id=str(row["user_id"]),
                profile_text=str(row["profile_text"]),
                last_compressed_at_ms=int(row["last_compressed_at_ms"]),
                updated_at_ms=int(row["updated_at_ms"]),
            )

        return await self._store_run(_exec)

    async def upsert_viewer_profile(
        self,
        *,
        platform: str,
        user_id: str,
        profile_text: str,
        last_compressed_at_ms: int,
    ) -> None:
        """写入/更新画像（增量压缩写回；水位一并推进）。"""
        text = (profile_text or "").strip()
        if not text:
            return
        ts = now_ms()

        def _exec() -> None:
            with self._manager_tx() as conn:
                conn.execute(
                    "INSERT INTO viewer_profiles(platform, user_id, profile_text, last_compressed_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(platform, user_id) DO UPDATE SET"
                    " profile_text=excluded.profile_text,"
                    " last_compressed_at_ms=excluded.last_compressed_at_ms,"
                    " updated_at_ms=excluded.updated_at_ms",
                    (platform, user_id, text, int(last_compressed_at_ms), ts),
                )

        await self._store_run(_exec)

    async def list_viewer_profiles(
        self,
        *,
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[int, List[ViewerProfile]]:
        """画像列表（WebUI 管理面）：搜索 + 分页，返回 ``(命中总数, 当页行)``。

        ``search`` 非空时对 profile_text / user_id 做 LIKE 匹配。
        """
        clauses: List[str] = []
        params: List[Any] = []
        keyword = (search or "").strip()
        if keyword:
            clauses.append("(profile_text LIKE ? OR user_id LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like])
        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        def _exec() -> Tuple[int, List[ViewerProfile]]:
            with self._manager_tx() as conn:
                total_row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM viewer_profiles{where_sql}",
                    tuple(params),  # noqa: S608
                ).fetchone()
                rows = conn.execute(
                    f"SELECT platform, user_id, profile_text, last_compressed_at_ms, updated_at_ms"  # noqa: S608
                    f" FROM viewer_profiles{where_sql} ORDER BY updated_at_ms DESC LIMIT ? OFFSET ?",
                    [*params, max(1, min(int(limit), 200)), max(0, int(offset))],
                ).fetchall()
            items = [
                ViewerProfile(
                    platform=str(row["platform"]),
                    user_id=str(row["user_id"]),
                    profile_text=str(row["profile_text"]),
                    last_compressed_at_ms=int(row["last_compressed_at_ms"]),
                    updated_at_ms=int(row["updated_at_ms"]),
                )
                for row in rows
            ]
            total = int(total_row["n"]) if total_row else 0
            return total, items

        return await self._store_run(_exec)

    async def update_viewer_profile_text(self, *, platform: str, user_id: str, profile_text: str) -> bool:
        """人工纠正画像文本（WebUI 管理面）；行不存在或空文本返回 False。"""
        text = (profile_text or "").strip()
        if not text:
            return False

        def _exec() -> bool:
            with self._manager_tx() as conn:
                cur = conn.execute(
                    "UPDATE viewer_profiles SET profile_text=?, updated_at_ms=? WHERE platform=? AND user_id=?",
                    (text, now_ms(), platform, user_id),
                )
                return cur.rowcount > 0

        return await self._store_run(_exec)

    async def delete_viewer_profile(self, *, platform: str, user_id: str) -> bool:
        """删除画像（WebUI 管理面）；行不存在返回 False。"""

        def _exec() -> bool:
            with self._manager_tx() as conn:
                cur = conn.execute(
                    "DELETE FROM viewer_profiles WHERE platform=? AND user_id=?",
                    (platform, user_id),
                )
                return cur.rowcount > 0

        return await self._store_run(_exec)

    # -------------------- 画像生成输入 --------------------

    async def list_profile_candidates(self, *, min_interactions: int) -> List[ViewerProfileSummary]:
        """列出待重建画像的观众：水位后有新事实且互动量达门槛。

        无画像行视为水位 0（首次生成）；门槛过滤 JOIN ``viewers`` 表的
        ``interaction_count``——不够格的观众不生成画像（自然不注入）。
        """

        def _exec() -> List[ViewerProfileSummary]:
            with self._manager_tx() as conn:
                rows = conn.execute(
                    "SELECT f.platform AS platform, f.user_id AS user_id,"
                    " COALESCE(v.interaction_count, 0) AS interaction_count"
                    " FROM viewer_facts f"
                    " LEFT JOIN viewer_profiles p ON p.platform=f.platform AND p.user_id=f.user_id"
                    " LEFT JOIN viewers v ON v.platform=f.platform AND v.user_id=f.user_id"
                    " GROUP BY f.platform, f.user_id"
                    " HAVING MAX(f.created_at_ms) > COALESCE(p.last_compressed_at_ms, 0)"
                    " AND COALESCE(v.interaction_count, 0) >= ?",
                    (int(min_interactions),),
                ).fetchall()
            return [
                ViewerProfileSummary(
                    platform=str(row["platform"]),
                    user_id=str(row["user_id"]),
                    interaction_count=int(row["interaction_count"]),
                )
                for row in rows
            ]

        return await self._store_run(_exec)

    # -------------------- 存储执行辅助 --------------------

    def _manager_tx(self) -> Any:
        """连接管理器事务上下文（``with`` 用法，同仓储层）。"""
        return self._store.manager.transaction()

    async def _store_run(self, fn: Callable[[], Any]) -> Any:
        """统一 ``asyncio.to_thread`` 防漏（同步 SQLite 调用不入事件循环）。"""
        return await asyncio.to_thread(fn)


__all__ = ["SimpleMemory"]
