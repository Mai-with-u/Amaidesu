"""
SimpleMemory —— 关键词召回实现

## 设计
- 内部使用 SQLiteDatabase，**复用业务表基座**（模块私有表建在同一库，事务在
  ``SQLiteDatabase`` 上走，避免分散存储后端）
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

对外接口 = 召回/写入承诺面（``recall`` / ``ingest``，经 ``MemoryProvider``
Protocol 面向 Agent 侧）+ 管理面（``list_facts`` / ``update_fact`` /
``delete_fact`` / ``stats``，面向 WebUI 记忆管理页的检索与清理）。

## 存储说明
本模块使用 SQLiteDatabase 数据库里 1 张**模块私有表**（``_`` 前缀表达"非业务
数据平面、仅 SimpleMemory 读写"；DDL 权威在 ``storage/schema.py``，随
``SQLiteDatabase.initialize()`` 统一建表，并纳入 ``SCHEMA_VERSION`` 版本管理）：
- ``_memory_facts``：事实/事件记忆条目

``SimpleMemory.initialize()`` 只做表自检，不带 DDL——建表职责单一归
schema.py，避免两处 DDL 漂移。

## 时间单位
- Amaidesu 内部全毫秒，本模块零转换
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from src.modules.logging import get_logger
from src.modules.memory.models import MemoryFact, MemoryHit, MemoryStats, MemoryWriteResult
from src.modules.memory.provider import MemoryProvider
from src.modules.storage.database import SQLiteDatabase
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

    def __init__(self, store: SQLiteDatabase) -> None:
        self._store = store

    async def initialize(self) -> None:
        """自检私有表已就位（DDL 由 SQLiteDatabase.initialize() 按 schema.py 统一建）。"""
        for table in ("_memory_facts",):
            if not await self._store.table_exists(table):
                raise RuntimeError(
                    f"SimpleMemory 私有表 {table} 不存在：请先执行 SQLiteDatabase.initialize()（建表 DDL 权威在 storage/schema.py）"
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

        row = await self._store.execute_returning(
            "INSERT INTO _memory_facts(text, source, tags, importance, timestamp_ms) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (text, source, tags_str, int(importance), ts),
        )
        new_id = -1
        if row is not None:
            try:
                new_id = int(row["id"])
            except (KeyError, TypeError, ValueError):
                new_id = -1
        return MemoryWriteResult(memory_id=new_id, accepted=True)

    # -------------------- 管理面（WebUI 记忆管理页消费） --------------------

    # 列表排序白名单：列名无法参数化，仅允许这几个确定性映射，防注入
    _LIST_ORDER_COLUMNS = {"timestamp_ms": "timestamp_ms", "importance": "importance"}

    @staticmethod
    def _row_to_fact(row: Any) -> MemoryFact:
        """``_memory_facts`` 查询行 → ``MemoryFact``（管理面统一投影）。"""
        return MemoryFact(
            memory_id=int(row["id"]),
            text=str(row["text"]),
            source=str(row["source"]),
            tags=str(row["tags"]),
            importance=int(row["importance"]),
            timestamp_ms=int(row["timestamp_ms"]),
        )

    async def list_facts(
        self,
        *,
        search: str = "",
        order_by: str = "timestamp_ms",
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[int, List[MemoryFact]]:
        """管理面列表：搜索 + 排序 + 分页，返回 ``(命中总数, 当页行)``。

        - ``search`` 非空时对 text / source / tags 做 LIKE 匹配（单关键词，
          与 recall 同为子串语义）
        - ``order_by`` 仅接受 ``timestamp_ms`` / ``importance``（白名单），
          其余值回落 ``timestamp_ms``；同为倒序
        - ``limit`` 收敛到 1..200，``offset`` 非负
        """
        where = ""
        params: List[Any] = []
        keyword = search.strip()
        if keyword:
            where = "WHERE text LIKE ? OR source LIKE ? OR tags LIKE ?"
            like = f"%{keyword}%"
            params = [like, like, like]

        order_col = self._LIST_ORDER_COLUMNS.get(order_by, "timestamp_ms")

        total_row = await self._store.execute_fetchone(
            f"SELECT COUNT(*) AS n FROM _memory_facts {where}", tuple(params)
        )
        total = int(total_row["n"]) if total_row is not None else 0

        rows = await self._store.execute(
            f"SELECT id, text, source, tags, importance, timestamp_ms FROM _memory_facts {where} "
            f"ORDER BY {order_col} DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params) + (max(1, min(int(limit), 200)), max(0, int(offset))),
        )
        return total, [self._row_to_fact(row) for row in rows]

    async def update_fact(
        self,
        memory_id: int,
        *,
        text: Optional[str] = None,
        tags: Any = None,
        importance: Optional[int] = None,
    ) -> bool:
        """管理面更新：仅落给定的字段（``None`` = 保持不变），返回是否有行被更新。

        - ``text`` 传空白串视为无效更新（记忆条目不允许空文本），返回 ``False``
        - ``tags`` 传列表/元组 → 逗号连接覆盖；空列表 → 清空；其余按字符串覆盖
        - id 不存在或无任何字段给出 → 返回 ``False``
        """
        assignments: List[str] = []
        params: List[Any] = []
        if text is not None:
            stripped = text.strip()
            if not stripped:
                return False
            assignments.append("text = ?")
            params.append(stripped)
        if tags is not None:
            assignments.append("tags = ?")
            params.append(",".join(tags) if isinstance(tags, (list, tuple)) else str(tags))
        if importance is not None:
            assignments.append("importance = ?")
            params.append(int(importance))
        if not assignments:
            return False
        params.append(int(memory_id))
        row = await self._store.execute_returning(
            f"UPDATE _memory_facts SET {', '.join(assignments)} WHERE id = ? RETURNING id",
            tuple(params),
        )
        return row is not None

    async def delete_fact(self, memory_id: int) -> bool:
        """管理面删除：返回是否确有行被删除（id 不存在时 ``False``，幂等安全）。"""
        row = await self._store.execute_returning(
            "DELETE FROM _memory_facts WHERE id = ? RETURNING id",
            (int(memory_id),),
        )
        return row is not None

    async def stats(self) -> MemoryStats:
        """管理面统计：总数 + 各来源计数（降序）+ 最新写入时刻（空库为 0）。"""
        total_row = await self._store.execute_fetchone(
            "SELECT COUNT(*) AS n, MAX(timestamp_ms) AS latest FROM _memory_facts"
        )
        total = int(total_row["n"]) if total_row is not None else 0
        latest_raw = total_row["latest"] if total_row is not None else None
        latest_ms = int(latest_raw) if latest_raw is not None else 0

        source_rows = await self._store.execute(
            "SELECT source, COUNT(*) AS n FROM _memory_facts GROUP BY source ORDER BY n DESC"
        )
        sources = [(str(row["source"]), int(row["n"])) for row in source_rows]
        return MemoryStats(total_facts=total, sources=sources, latest_ms=latest_ms)


__all__ = ["SimpleMemory"]
