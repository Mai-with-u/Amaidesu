"""
SimpleMemory —— 关键词召回实现（Wave 3 / §1.50）

## 设计
- 内部使用 SQLiteStore，**复用 11 表基座**（个人 SQL 层另起二表，事务在
  ``SQLiteStore`` 上走，避免分散存储后端）
- 召回 = 简单 LIKE 关键词匹配（不使用 embedding，§1.50 简单版）
- 维护（maintain）= 简单按 timestamp_ms 衰减分数；空实现基准

## 存储说明
本模块在 SQLiteStore 数据库里**追加** 2 张表（与 11 表不在权威 schema 中，
仅 SimpleMemory 内部用，命名加 ``_memory_`` 前缀避免与 11 表冲突）：
- ``_memory_facts``：事实/事件记忆条目
- ``_memory_profiles``：观众语义画像

这两张表不是 wave-3 schema 权威定义的 11 张；SimpleMemory 内部数据隔离，
未来接 A_Memorix 时整体替换。

## 时间单位
- Amaidesu 内部全毫秒（§1.53 9d）
- 接 A_Memorix 时在 adapter 层 ms → s（time_utils.ms_to_s/s_to_ms）——SimpleMemory
  走毫秒，零转换
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.modules.logging import get_logger
from src.modules.memory.models import MemoryHit, MemoryWriteResult, PersonProfile
from src.modules.memory.provider import MemoryProvider
from src.modules.storage.sqlite_store import SQLiteStore
from src.modules.time_utils import now_ms

logger = get_logger("SimpleMemory")


# SimpleMemory 私有 DDL（不属 11 表，仅 SimpleMemory 后端使用）
_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS _memory_facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT '',
    tags          TEXT NOT NULL DEFAULT '',
    importance    INTEGER NOT NULL DEFAULT 0,
    timestamp_ms  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS _memory_profiles (
    person_id       TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    updated_at_ms   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_facts_timestamp ON _memory_facts(timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_memory_facts_source ON _memory_facts(source);
"""


class SimpleMemory(MemoryProvider):
    """关键词召回的记忆实现（SQLite 持久化）。"""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    async def initialize(self) -> None:
        """初始化：建私有表（幂等）。"""
        await self._store.execute_script(_SCHEMA_DDL)
        logger.debug("SimpleMemory 私有表已就绪")

    # -------------------- 接口实现 --------------------

    async def recall(self, query: str, top_k: int = 5) -> List[MemoryHit]:
        """关键词召回：对 _memory_facts 做 LIKE 匹配；O(n) 子集扫描足以起步。"""
        if not query or not query.strip():
            return []
        keywords = [k for k in query.replace("\n", " ").split(" ") if len(k) >= 2][:8]
        if not keywords:
            return []

        # 简化：用 LIKE %kw% 多 OR（关键词数少；SQLite 起步够用；后续可换 FTS5）
        conditions = []
        params: List[Any] = []
        for kw in keywords:
            conditions.append("text LIKE ?")
            params.append(f"%{kw}%")

        sql = (
            "SELECT id, text, source, tags, importance, timestamp_ms FROM _memory_facts "
            "WHERE " + " OR ".join(conditions) + " "
            "ORDER BY importance DESC, timestamp_ms DESC LIMIT ?"
        )
        params.append(int(top_k))

        rows = await self._store.execute(sql, tuple(params))
        hits: List[MemoryHit] = []
        for row in rows:
            hits.append(
                MemoryHit(
                    memory_id=int(row["id"]),
                    kind="fact",
                    text=str(row["text"]),
                    score=float(row["importance"]) + 1.0,
                    timestamp_ms=int(row["timestamp_ms"]),
                    metadata={"source": row["source"], "tags": row["tags"]},
                )
            )
        return hits

    async def ingest(
        self,
        text: str,
        source: str = "",
        *,
        tags: Any = None,
        timestamp_ms: int = 0,
        importance: int = 0,
    ) -> MemoryWriteResult:
        """写入一条事实记忆。"""
        if not text or not text.strip():
            return MemoryWriteResult(memory_id=-1, accepted=False, message="空文本")
        ts = int(timestamp_ms or now_ms())
        tags_str = ",".join(tags) if isinstance(tags, (list, tuple)) else (str(tags) if tags else "")

        rows = await self._store.execute_returning(
            "INSERT INTO _memory_facts(text, source, tags, importance, timestamp_ms) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (text, source, tags_str, int(importance), ts),
        )
        try:
            new_id = int(rows["id"])
        except (KeyError, TypeError, ValueError):
            new_id = -1
        return MemoryWriteResult(memory_id=new_id, accepted=True)

    async def get_person_profile(self, person_id: str) -> PersonProfile:
        """读取观众画像；缺记录返回空画像。"""
        if not person_id:
            return PersonProfile(person_id="")
        row = await self._store.execute_fetchone(
            "SELECT person_id, display_name, tags, summary, updated_at_ms FROM _memory_profiles WHERE person_id = ?",
            (person_id,),
        )
        if row is None:
            return PersonProfile(person_id=person_id)
        tags_str = str(row["tags"] or "")
        tags_list = [t for t in tags_str.split(",") if t] if tags_str else []
        return PersonProfile(
            person_id=str(row["person_id"]),
            display_name=str(row["display_name"] or ""),
            tags=tags_list,
            summary=str(row["summary"] or ""),
            updated_at_ms=int(row["updated_at_ms"] or 0),
        )

    async def upsert_person_profile(
        self,
        person_id: str,
        *,
        display_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        summary: Optional[str] = None,
    ) -> PersonProfile:
        """创建/合并更新观众画像（LLM 总结完调用）。"""
        if not person_id:
            raise ValueError("person_id 不能为空")

        existing = await self.get_person_profile(person_id)
        merged_name = display_name if display_name is not None else existing.display_name
        merged_tags = tags if tags is not None else existing.tags
        merged_summary = summary if summary is not None else existing.summary

        # 去重 + 稳定排序
        seen = set()
        merged_tags = [t for t in merged_tags if not (t in seen or seen.add(t))]

        ts = now_ms()
        tags_str = ",".join(merged_tags)
        # 使用参数化占位符避免 SQL 注入（Oracle 审查：原实现 f-string 拼接存在
        # 注入风险，且 _sql_escape 仅转义单引号，对分号/双引号/反斜杠无防护）。
        await self._store.execute(
            "INSERT INTO _memory_profiles(person_id, display_name, tags, summary, updated_at_ms) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(person_id) DO UPDATE SET "
            "display_name=excluded.display_name, tags=excluded.tags, "
            "summary=excluded.summary, updated_at_ms=excluded.updated_at_ms",
            (person_id, merged_name, tags_str, merged_summary, ts),
        )
        return PersonProfile(
            person_id=person_id,
            display_name=merged_name,
            tags=merged_tags,
            summary=merged_summary,
            updated_at_ms=ts,
        )

    async def maintain(self) -> None:
        """占位：未来可加 LRU/衰减/合并。当前不做事。"""
        # 留口子：稳定接口里给"维护"一个位置，但不强制做事
        return None


__all__ = ["SimpleMemory"]
