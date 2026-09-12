"""
SimpleMemory —— 关键词召回实现

## 设计
- 内部使用 SQLiteStore，**复用业务表基座**（模块私有表建在同一库，事务在
  ``SQLiteStore`` 上走，避免分散存储后端）
- 召回 = 简单 LIKE 关键词匹配（不使用 embedding）

## 召回分词策略

主场景是中文弹幕直播间：直接 ``query.split(" ")`` 对"弹幕互动有什么"这种
无空格中文句子只产出**一个**长 token，整段作为 LIKE 模式几乎零命中
（实际文本里不会原样出现），故用 ``_extract_keywords`` 启发式分词：

- 第一阶段切分：按空白 + 中英文常见标点切（保留 CJK 段完整）
- 第二阶段按类型处理：
  - ASCII 英文/数字词（长度 ≥ 2）→ 整段保留（"minecraft"、"OAI" 等）
  - CJK 连续段：
    - 长度 2-6 → 取整段
    - 长度 > 6 → 2-gram 滑动窗口（如"弹幕互动有什么" → "弹幕"、"幕互"、
      "互动"、"动有"、"有什"、"什么"）
- 去重 + 保序 + 截断到 ``max_keywords``

**已知局限**（后续可换 FTS5 / jieba）：
- 不分语义边界（"我想看动漫" → "我想"、"想看"、"看动"、"动漫"，无意义 2-gram 噪声）
- 不区分停用词（"的/了/是"会被当 2-gram 命中）
- 无词性标注、无归一化（"弹幕"/"彈幕" 视为不同 token）

对外接口 = ``recall``（召回）与 ``ingest``（写入），签名即承诺面。

## 存储说明
本模块使用 SQLiteStore 数据库里 1 张**模块私有表**（``_`` 前缀表达"非业务
数据平面、仅 SimpleMemory 读写"；DDL 权威在 ``storage/schema.py``，随
``SQLiteStore.initialize()`` 统一建表，并纳入 ``SCHEMA_VERSION`` 版本管理）：
- ``_memory_facts``：事实/事件记忆条目

``SimpleMemory.initialize()`` 只做表自检，不带 DDL——建表职责单一归
schema.py，避免两处 DDL 漂移。

## 时间单位
- Amaidesu 内部全毫秒，本模块零转换
"""

from __future__ import annotations

import re
from typing import Any, List

from src.modules.logging import get_logger
from src.modules.memory.models import MemoryHit, MemoryWriteResult
from src.modules.memory.provider import MemoryProvider
from src.modules.storage.sqlite_store import SQLiteStore
from src.modules.time_utils import now_ms

logger = get_logger("SimpleMemory")


# =============================================================================
# 分词启发式（CJK-aware）
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


class SimpleMemory(MemoryProvider):
    """关键词召回的记忆实现（SQLite 持久化）。"""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    async def initialize(self) -> None:
        """自检私有表已就位（DDL 由 SQLiteStore.initialize() 按 schema.py 统一建）。"""
        for table in ("_memory_facts",):
            if not await self._store.table_exists(table):
                raise RuntimeError(
                    f"SimpleMemory 私有表 {table} 不存在：请先执行 SQLiteStore.initialize()（建表 DDL 权威在 storage/schema.py）"
                )
        logger.debug("SimpleMemory 私有表自检通过")

    # -------------------- 接口实现 --------------------

    async def recall(self, query: str, top_k: int = 5) -> List[MemoryHit]:
        """关键词召回：对 _memory_facts 做 LIKE 匹配；O(n) 子集扫描足以起步。"""
        if not query or not query.strip():
            return []
        # CJK-aware 分词（中文无空格句子需切出可命中的子串关键词）
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


__all__ = ["SimpleMemory"]
