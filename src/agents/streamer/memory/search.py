"""记忆的混合检索：关键词（FTS5 两字切分 + BM25）与语义（向量余弦）两路，按名次融合（RRF）。

给回想工具与被动想起共用，只回答"哪些来源行最相关"，不渲染、不遮名、不判存档——那些是
调用方按自己的口径做的。收什么、查询时再核什么（正式场、没标没播出、不是误记）由索引仓储定。

- **关键词一路**：查询文本两字切分后在 FTS5 里做"或"查询，按 BM25 取前 50。查询词里有单个汉字
  （"炸TNT"的"炸"、单独查的"猫"）时按词查必漏，另用子串匹配兜底，作为单独一路参与融合。
- **语义一路**：查询向量与存下的向量算余弦取前 50。查询向量按查询文本缓存；调用方说等多久——
  回想工具可以等一小会儿，每批都要算的被动想起不等（这一批只走关键词，向量算好留给下一批）。
  没配嵌入服务、或嵌入失败时这一路空着，检索照样有结果。
- **融合**：分数 = Σ 1/(60 + 名次)，每一路各贡献一次（60 取 RRF 论文的常用值）。不同来源
  （档案、承诺、台词、事件）的各路名单一起融合，结果里带着每一路的名次与分数，给日志和复盘面板看。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from src.modules.llm.embedding import EmbeddingClient, EmbeddingError
from src.modules.logging import get_logger
from src.modules.storage.models.memory import IndexCandidate
from src.modules.storage.repos.memory_index import INDEX_SOURCES, IndexSource, MemoryIndexRepo

from .textmatch import fts_or_query, needs_like_fallback

logger = get_logger("MemorySearch")

# RRF 的平滑常数
RRF_K = 60
# 每一路每类来源取多少个候选
CANDIDATES_PER_PATH = 50
# 查询向量缓存多少条查询文本
QUERY_VECTOR_CACHE_SIZE = 64


@dataclass(frozen=True)
class SearchHit:
    """一条检索结果：哪类来源的哪一行、融合分，以及各路的名次与分数（没被这一路找到为空）。"""

    source: IndexSource
    source_id: int
    score: float
    keyword_rank: Optional[int] = None
    keyword_score: Optional[float] = None
    like_rank: Optional[int] = None
    vector_rank: Optional[int] = None
    vector_score: Optional[float] = None


@dataclass(frozen=True)
class SearchResult:
    """一次检索的结果与这一次语义一路有没有用上（没配嵌入、没算好、算失败都算没用上）。"""

    hits: List[SearchHit]
    vector_used: bool


@dataclass
class _Fused:
    """融合过程中的一条：累加的分数与各路的名次分数。"""

    score: float = 0.0
    keyword: Optional[Tuple[int, float]] = None
    like: Optional[int] = None
    vector: Optional[Tuple[int, float]] = None


class MemorySearch:
    """混合检索：构造时注入索引仓储与（可选的）嵌入客户端。"""

    def __init__(self, *, repo: MemoryIndexRepo, embedding: Optional[EmbeddingClient]) -> None:
        self._repo = repo
        self._embedding = embedding
        self._query_vectors: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._pending: Dict[str, "asyncio.Task[None]"] = {}
        self._failing = False

    async def search(
        self,
        query: str,
        *,
        sources: Sequence[IndexSource] = INDEX_SOURCES,
        live_session_id: Optional[int] = None,
        limit: int = 8,
        wait_vector_ms: int = 0,
    ) -> List[SearchHit]:
        """查一段文本，返回融合后最相关的 ``limit`` 条；``live_session_id`` 给了就只查那一场。"""
        result = await self.search_detailed(
            query, sources=sources, live_session_id=live_session_id, limit=limit, wait_vector_ms=wait_vector_ms
        )
        return result.hits

    async def search_detailed(
        self,
        query: str,
        *,
        sources: Sequence[IndexSource] = INDEX_SOURCES,
        live_session_id: Optional[int] = None,
        limit: int = 8,
        wait_vector_ms: int = 0,
    ) -> SearchResult:
        """同 ``search``，另外告诉调用方这一次语义一路有没有用上（回想工具据此提醒"只按字面查"）。"""
        text = (query or "").strip()
        if not text:
            return SearchResult(hits=[], vector_used=False)
        fused: Dict[Tuple[IndexSource, int], _Fused] = {}
        match_query = fts_or_query(text)
        needles = [term for term in text.split() if needs_like_fallback(term)]
        query_vector = await self._query_vector(text, wait_vector_ms)
        for source in sources:
            if match_query is not None:
                found = await self._repo.keyword_candidates(
                    source=source, match_query=match_query, limit=CANDIDATES_PER_PATH, live_session_id=live_session_id
                )
                for rank, candidate in enumerate(found, start=1):
                    entry = fused.setdefault((source, candidate.source_id), _Fused())
                    entry.score += 1 / (RRF_K + rank)
                    entry.keyword = (rank, candidate.score)
            if needles:
                for rank, source_id in enumerate(await self._like_ids(source, needles, live_session_id), start=1):
                    entry = fused.setdefault((source, source_id), _Fused())
                    entry.score += 1 / (RRF_K + rank)
                    entry.like = rank
            if query_vector is not None and self._embedding is not None:
                for rank, (source_id, cosine) in enumerate(
                    await self._vector_candidates(self._embedding, source, query_vector, live_session_id), start=1
                ):
                    entry = fused.setdefault((source, source_id), _Fused())
                    entry.score += 1 / (RRF_K + rank)
                    entry.vector = (rank, cosine)
        ranked = sorted(fused.items(), key=lambda item: item[1].score, reverse=True)[: max(0, limit)]
        hits = [
            SearchHit(
                source=source,
                source_id=source_id,
                score=entry.score,
                keyword_rank=entry.keyword[0] if entry.keyword else None,
                keyword_score=entry.keyword[1] if entry.keyword else None,
                like_rank=entry.like,
                vector_rank=entry.vector[0] if entry.vector else None,
                vector_score=entry.vector[1] if entry.vector else None,
            )
            for (source, source_id), entry in ranked
        ]
        return SearchResult(hits=hits, vector_used=query_vector is not None and self._embedding is not None)

    async def _like_ids(self, source: IndexSource, needles: Sequence[str], live_session_id: Optional[int]) -> List[int]:
        """子串兜底这一路：几个需要兜底的词各查一遍，按先后合并去重。"""
        merged: List[int] = []
        seen: Set[int] = set()
        for needle in needles:
            found: List[IndexCandidate] = await self._repo.like_candidates(
                source=source, needle=needle, limit=CANDIDATES_PER_PATH, live_session_id=live_session_id
            )
            for candidate in found:
                if candidate.source_id not in seen:
                    seen.add(candidate.source_id)
                    merged.append(candidate.source_id)
        return merged[:CANDIDATES_PER_PATH]

    async def _vector_candidates(
        self, embedding: EmbeddingClient, source: IndexSource, query_vector: np.ndarray, live_session_id: Optional[int]
    ) -> List[Tuple[int, float]]:
        """语义这一路：当前模型的全部向量与查询向量算余弦，取最高的一批。"""
        stored = [
            item
            for item in await self._repo.stored_vectors(
                source=source, model_key=embedding.model_key, live_session_id=live_session_id
            )
            if item.vector
        ]
        if not stored:
            return []
        matrix = np.stack([np.frombuffer(item.vector, dtype="<f4") for item in stored])
        if matrix.shape[1] != query_vector.shape[0]:
            logger.warning(
                f"{source} 的向量维度 {matrix.shape[1]} 与查询向量 {query_vector.shape[0]} 不一致，这一路跳过"
            )
            return []
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1.0
        cosines = (matrix @ query_vector) / norms
        order = np.argsort(-cosines)[:CANDIDATES_PER_PATH]
        return [(stored[int(index)].source_id, float(cosines[int(index)])) for index in order]

    @property
    def semantic_ready(self) -> bool:
        """配了嵌入服务（语义一路能用；每次查询算不算得出向量另说）。"""
        return self._embedding is not None

    def vector_ready(self, query: str) -> bool:
        """这段查询文本的向量已经算好缓存着；没有就在后台开始算，这次返回 False（下一批多半能用）。"""
        text = (query or "").strip()
        if not text or self._embedding is None:
            return False
        if text in self._query_vectors:
            return True
        self._ensure_embedding_task(text)
        return False

    def prefetch(self, query: str) -> None:
        """提前在后台算查询向量（攒批窗口一开就调，组装上下文时多半已经算好）。"""
        text = (query or "").strip()
        if text and self._embedding is not None:
            self._ensure_embedding_task(text)

    async def _query_vector(self, text: str, wait_ms: int) -> Optional[np.ndarray]:
        """取查询向量：缓存里有就用；没有就发起计算，最多等 ``wait_ms``，等不到这次就不用。"""
        if self._embedding is None:
            return None
        cached = self._query_vectors.get(text)
        if cached is not None:
            self._query_vectors.move_to_end(text)
            return cached
        task = self._ensure_embedding_task(text)
        if wait_ms <= 0:
            return None
        done, _pending = await asyncio.wait({task}, timeout=wait_ms / 1000)
        return self._query_vectors.get(text) if done else None

    def _ensure_embedding_task(self, text: str) -> "asyncio.Task[None]":
        task = self._pending.get(text)
        if task is None:
            task = asyncio.create_task(self._embed_query(text))
            self._pending[text] = task
        return task

    async def _embed_query(self, text: str) -> None:
        """算一条查询向量（单位化后缓存）；失败只在连续失败的第一次记 warning，免得每批刷屏。"""
        embedding = self._embedding
        if embedding is None:
            self._pending.pop(text, None)
            return
        try:
            (vector,) = await embedding.embed([text])
        except EmbeddingError as exc:
            if not self._failing:
                logger.warning(f"查询向量算不出来，这期间检索只走关键词：{exc}")
            self._failing = True
            return
        finally:
            self._pending.pop(text, None)
        self._failing = False
        array = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(array))
        self._query_vectors[text] = array / norm if norm else array
        while len(self._query_vectors) > QUERY_VECTOR_CACHE_SIZE:
            self._query_vectors.popitem(last=False)


__all__ = ["CANDIDATES_PER_PATH", "RRF_K", "MemorySearch", "SearchHit", "SearchResult"]
