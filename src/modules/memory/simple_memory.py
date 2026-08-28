"""
SimpleMemory —— 关键词召回实现（Wave 3 / §1.50）

## 设计
- 内部使用 SQLiteStore，**复用 11 表基座**（个人 SQL 层另起二表，事务在
  ``SQLiteStore`` 上走，避免分散存储后端）
- 召回 = 简单 LIKE 关键词匹配（不使用 embedding，§1.50 简单版）
- 维护（maintain）= 简单按 timestamp_ms 衰减分数；空实现基准

## 召回分词策略（§1.50 + Wave 8 中文召回修复）

主场景是中文弹幕直播间：原始实现 ``query.split(" ")`` 对"弹幕互动有什么"
这种无空格中文句子只产出**一个**长 token，"弹幕互动有什么" 整段作为 LIKE 模式
几乎零命中（实际文本里不会原样出现）。改为 ``_extract_keywords`` 启发式：

1. **第一阶段切分**：按空白 + 中英文常见标点切（保留 CJK 段完整）
2. **第二阶段按类型处理**：
   - ASCII 英文/数字词（长度 ≥ 2）→ 整段保留（"minecraft"、"OAI" 等）
   - CJK 连续段：
     - 长度 2-6 → 取整段
     - 长度 > 6 → 2-gram 滑动窗口（如"弹幕互动有什么" → "弹幕"、"幕互"、
       "互动"、"动有"、"有什"、"什么"）
3. **去重 + 保序 + 截断到 ``max_keywords``**

**已知局限**（后续可换 FTS5 / jieba）：
- 不分语义边界（"我想看动漫" → "我想"、"想看"、"看动"、"动漫"，无意义 2-gram 噪声）
- 不区分停用词（"的/了/是"会被当 2-gram 命中）
- 无词性标注、无归一化（"弹幕"/"彈幕" 视为不同 token）

接口冻结（Streamer Agent 等并行模块依赖现状）：
``recall / ingest / get_person_profile / upsert_person_profile / maintain``
签名禁止修改。

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

import re
from typing import Any, List, Optional

from src.modules.logging import get_logger
from src.modules.memory.models import MemoryHit, MemoryWriteResult, PersonProfile
from src.modules.memory.provider import MemoryProvider
from src.modules.storage.sqlite_store import SQLiteStore
from src.modules.time_utils import now_ms

logger = get_logger("SimpleMemory")


# =============================================================================
# 分词启发式（Wave 8 / 中文召回修复）
# =============================================================================

# CJK 统一表意 + 扩展 A（覆盖 99% 现代中文；扩展 B-F 罕用词不覆盖以省 regex）
_CJK_SEGMENT = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")

# 空白 + 中英文常见标点（CJK 字符本身不在内，得以保留为连续段）
_SPLITTER = re.compile(
    r"[\s\u3000"
    r"\u3001\u3002"
    r"\uff0c\uff01\uff1f\uff1b\uff1a"
    r"\u201c\u201d\u2018\u2019"
    r"\u300a\u300b\u3008\u3009"
    r"\uff08\uff09\u3010\u3011"
    r"\.\!\?\,\;\:\(\)\[\]\{\}\<\>\/\\\|\+\=\-\_\*"
    r"]+"
)

# ASCII 英文 / 数字 连续段
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")


def _extract_keywords(query: str, max_keywords: int = 8) -> List[str]:
    """从 ``query`` 中抽取用于 LIKE 召回的关键词列表。

    策略详见模块 docstring "召回分词策略"一节。

    Args:
        query: 用户查询文本（可能含中文 / 英文 / 数字 / 标点混合）
        max_keywords: 最大返回关键词数（默认 8，对齐 LIKE 性能 + 噪声抑制）

    Returns:
        去重 + 保序的关键词列表。空查询 / 无有效 token 时返回空列表。
    """
    if not query or not isinstance(query, str):
        return []
    cleaned = query.strip()
    if not cleaned:
        return []

    tokens = [t for t in _SPLITTER.split(cleaned) if t]
    if not tokens:
        return []

    seen: set[str] = set()
    result: List[str] = []

    def _try_add(keyword: str) -> bool:
        """尝试去重添加；返回 ``True`` 表示已触发 ``max_keywords`` 截断。"""
        if not keyword or keyword in seen:
            return False
        seen.add(keyword)
        result.append(keyword)
        return len(result) >= max_keywords

    for token in tokens:
        # ASCII 英文/数字词：长度 ≥ 2 才保留（避免 1-char 噪声）
        for m in _ASCII_WORD.finditer(token):
            word = m.group()
            if len(word) >= 2 and _try_add(word):
                return result[:max_keywords]

        # CJK 连续段：按长度分桶处理
        for m in _CJK_SEGMENT.finditer(token):
            seg = m.group()
            length = len(seg)
            if length < 2:
                # 单字 CJK 召回噪声大（命中停用词/通用字），跳过
                continue
            if length <= 6:
                if _try_add(seg):
                    return result[:max_keywords]
            else:
                # 长度 > 6：2-gram 滑动窗口（任何子串都有命中概率）
                for i in range(length - 1):
                    gram = seg[i : i + 2]
                    if _try_add(gram):
                        return result[:max_keywords]

    return result[:max_keywords]


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
        # Wave 8 中文召回修复：CJK-aware 分词；不再用 query.split(" ")
        keywords = _extract_keywords(query, max_keywords=8)
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
