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
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from src.modules.llm.embedding import EmbeddingClient, EmbeddingError
from src.modules.logging import get_logger
from src.modules.storage.models.memory import IndexCandidate
from src.modules.storage.repos.memory_index import INDEX_SOURCES, IndexSource, MemoryIndexRepo

from .textmatch import (
    bigram_tokens,
    content_tokens,
    fts_recall_query,
    is_stopword_token,
    needs_like_fallback,
    recall_query_tokens,
)

logger = get_logger("MemorySearch")

# RRF 的平滑常数
RRF_K = 60
# 每一路每类来源取多少个候选
CANDIDATES_PER_PATH = 50
# 查询向量缓存多少条查询文本
QUERY_VECTOR_CACHE_SIZE = 64
# 同分档微幅新近度偏置上限（约 1 个 RRF 名次差）：仅在相关度几乎平分时优先排近期状态更新，不跨档反超高相关早期事实
RECENCY_MAX_BONUS = 0.00028
# 判定两条候选属于同一相关度档的容差：关键词 BM25 分差不超过 1.5%，语义余弦差不超过 0.005
_KEYWORD_BAND_RATIO = 0.985
_VECTOR_BAND_DELTA = 0.005
# 台词还原拼合窗口长度上限（超过此长度无法完整拼入前邻句，检索层不让超长后句白蹭前句词分）
_SPEECH_WINDOW_FIT_CHARS = 140


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
    used_stopword_fallback: bool = False


def _rrf_with_band_recency(
    items: Sequence[Tuple[int, float]],
    *,
    same_band: Callable[[float, float], bool],
) -> List[Tuple[int, int, float, float]]:
    """按单路排名计算带同分档微幅新近度偏置的 RRF 贡献：(rank, source_id, raw_score, rrf_contribution)。

    只在原始相关度接近的同分档内部（档内至少 2 条候选）按主键新旧给至多 ``RECENCY_MAX_BONUS`` 的微幅加分；
    独占单条分档的高相关早期事实加分为 0，且后续分档的天花板严格低于上一分档末名，杜绝低相关新记录跨档反超。
    """
    if not items:
        return []
    ordered_ids = sorted({source_id for source_id, _score in items})
    id_span = len(ordered_ids) - 1
    recency_pos = {source_id: (idx / id_span if id_span > 0 else 0.0) for idx, source_id in enumerate(ordered_ids)}

    bands: List[List[Tuple[int, int, float]]] = []
    for rank, (source_id, raw_score) in enumerate(items, start=1):
        if not bands or not same_band(bands[-1][0][2], raw_score):
            bands.append([(rank, source_id, raw_score)])
        else:
            bands[-1].append((rank, source_id, raw_score))

    contributions: List[Tuple[int, int, float, float]] = []
    for band in bands:
        band_start = band[0][0]
        head_rrf = 1.0 / (RRF_K + band_start)
        if len(band) == 1:
            rank, source_id, raw_score = band[0]
            contributions.append((rank, source_id, raw_score, head_rrf))
            continue
        if band_start == 1:
            ceiling = head_rrf + RECENCY_MAX_BONUS
        else:
            prev_floor = 1.0 / (RRF_K + band_start - 1)
            ceiling = head_rrf + 0.5 * (prev_floor - head_rrf)
        for rank, source_id, raw_score in band:
            base = 1.0 / (RRF_K + rank)
            boosted = min(ceiling, base + RECENCY_MAX_BONUS * recency_pos[source_id])
            contributions.append((rank, source_id, raw_score, boosted))
    return contributions


def _same_keyword_band(head_score: float, score: float) -> bool:
    if head_score <= 0.0:
        return abs(head_score - score) <= 1e-6
    return score >= head_score * _KEYWORD_BAND_RATIO


def _same_vector_band(head_score: float, score: float) -> bool:
    return score >= head_score - _VECTOR_BAND_DELTA


def _de_echo_speech_keywords(
    items: Sequence[Tuple[int, float]],
    windows: Mapping[int, Tuple[str, Optional[int], str]],
    query_terms: Set[str],
) -> List[Tuple[int, float]]:
    """消除台词前邻句滑窗带来的蹭词错位：后句无跨句新词或超长装不下前句时，把首发主分归还前句并按本句覆盖比折算。"""
    if not items or not windows or not query_terms:
        return list(items)
    adjusted: Dict[int, float] = {}
    for cid, score in items:
        win = windows.get(cid)
        if win is None or win[1] is None:
            adjusted[cid] = max(adjusted.get(cid, 0.0), score)
            continue
        content, prev_id, prev_content = win
        curr_own = set(bigram_tokens(content)) & query_terms
        prev_own = set(bigram_tokens(prev_content)) & query_terms
        new_terms = curr_own - prev_own
        if not new_terms and prev_own:
            # 后句未带来前邻句以外的任何新查询词：双倍词频涨出的高分归还给首次说全的前邻句
            adjusted[prev_id] = max(adjusted.get(prev_id, 0.0), score)
            if curr_own:
                ratio = (len(curr_own) / len(prev_own)) * 0.72
                adjusted[cid] = max(adjusted.get(cid, 0.0), score * ratio)
            continue
        if len(content) + len(prev_content) + 1 > _SPEECH_WINDOW_FIT_CHARS and prev_own:
            # 两句总长超过 140 字无法完整拼成一条展示时，按各自词覆盖比例拆分，避免超长后话挤掉真正含词的前句
            union_size = max(1, len(curr_own | prev_own))
            if len(prev_own) >= len(curr_own):
                adjusted[prev_id] = max(adjusted.get(prev_id, 0.0), score * (len(prev_own) / union_size))
            adjusted[cid] = max(adjusted.get(cid, 0.0), score * (len(curr_own) / union_size))
            continue
        adjusted[cid] = max(adjusted.get(cid, 0.0), score)
    return sorted(adjusted.items(), key=lambda pair: pair[1], reverse=True)[:CANDIDATES_PER_PATH]


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

    async def search(
        self,
        query: str,
        *,
        sources: Sequence[IndexSource] = INDEX_SOURCES,
        live_session_id: Optional[int] = None,
        limit: int = 8,
        wait_vector_ms: int = 0,
        min_cosine: float = 0.0,
    ) -> List[SearchHit]:
        """查一段文本，返回融合后最相关的 ``limit`` 条；``live_session_id`` 给了就只查那一场。"""
        result = await self.search_detailed(
            query,
            sources=sources,
            live_session_id=live_session_id,
            limit=limit,
            wait_vector_ms=wait_vector_ms,
            min_cosine=min_cosine,
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
        min_cosine: float = 0.0,
    ) -> SearchResult:
        """同 ``search``，另外告诉调用方这一次语义一路有没有用上（回想工具据此提醒"只按字面查"）。"""
        text = (query or "").strip()
        if not text:
            return SearchResult(hits=[], vector_used=False)
        fused: Dict[Tuple[IndexSource, int], _Fused] = {}
        match_query = fts_recall_query(text)
        query_terms = set(recall_query_tokens(text))
        has_content_terms = bool(content_tokens(text))
        raw_needles = [term for term in text.split() if needs_like_fallback(term)]
        # 查询里已有实词时，单字口语虚词（如"我""的"）不再触发全表 LIKE 兜底，免得把近期闲聊塞进融合池
        needles = [term for term in raw_needles if not is_stopword_token(term)] if has_content_terms else raw_needles
        query_vector = await self._query_vector(text, wait_vector_ms)
        for source in sources:
            kw_items: List[Tuple[int, float]] = []
            if match_query is not None:
                kw_limit = CANDIDATES_PER_PATH * 2 if source == "speech" else CANDIDATES_PER_PATH
                found = await self._repo.keyword_candidates(
                    source=source, match_query=match_query, limit=kw_limit, live_session_id=live_session_id
                )
                kw_items = [(candidate.source_id, candidate.score) for candidate in found]
            vec_items: List[Tuple[int, float]] = []
            if query_vector is not None and self._embedding is not None:
                vec_items = await self._vector_candidates(
                    self._embedding, source, query_vector, live_session_id, min_cosine=min_cosine
                )
            if source == "speech" and kw_items and query_terms:
                window_fn = getattr(self._repo, "speech_window_rows", None)
                if callable(window_fn):
                    windows = await window_fn(chat_ids=[cid for cid, _ in kw_items])
                    kw_items = _de_echo_speech_keywords(kw_items, windows, query_terms)
            for rank, source_id, kw_score, rrf_part in _rrf_with_band_recency(kw_items, same_band=_same_keyword_band):
                entry = fused.setdefault((source, source_id), _Fused())
                entry.score += rrf_part
                entry.keyword = (rank, kw_score)
            if needles:
                for rank, source_id in enumerate(await self._like_ids(source, needles, live_session_id), start=1):
                    entry = fused.setdefault((source, source_id), _Fused())
                    entry.score += 1 / (RRF_K + rank)
                    entry.like = rank
            for rank, source_id, cosine, rrf_part in _rrf_with_band_recency(vec_items, same_band=_same_vector_band):
                entry = fused.setdefault((source, source_id), _Fused())
                entry.score += rrf_part
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
        return SearchResult(
            hits=hits,
            vector_used=query_vector is not None and self._embedding is not None,
            used_stopword_fallback=match_query is not None and not has_content_terms,
        )

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
        self,
        embedding: EmbeddingClient,
        source: IndexSource,
        query_vector: np.ndarray,
        live_session_id: Optional[int],
        *,
        min_cosine: float = 0.0,
    ) -> List[Tuple[int, float]]:
        """语义这一路：当前模型的全部向量与查询向量算余弦，丢弃低于 ``min_cosine`` 的低分噪声后取最高的一批。"""
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
        return [
            (stored[int(index)].source_id, float(cosines[int(index)]))
            for index in order
            if float(cosines[int(index)]) >= min_cosine
        ]

    @property
    def semantic_ready(self) -> bool:
        """配了嵌入服务（语义一路能用；每次查询算不算得出向量另说）。"""
        return self._embedding is not None

    def vector_ready(self, query: str, *, start: bool = True) -> bool:
        """这段查询文本的向量已经算好缓存着就返回 True。

        没算好时 ``start`` 为真就在后台开始算（下一批多半能用）；为假只看缓存——弹幕到达时已经预取过
        一次，组装时没算好（还在算、算失败、在退避）就这一批不用，不再补发。
        """
        text = (query or "").strip()
        if not text or self._embedding is None:
            return False
        if text in self._query_vectors:
            return True
        if start:
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
        if task is None or wait_ms <= 0:
            return None
        done, _pending = await asyncio.wait({task}, timeout=wait_ms / 1000)
        return self._query_vectors.get(text) if done else None

    def _ensure_embedding_task(self, text: str) -> Optional["asyncio.Task[None]"]:
        """同一段文本同时只算一次；嵌入客户端在退避时不开新任务（这期间只走关键词）。"""
        task = self._pending.get(text)
        if task is not None:
            return task
        if self._embedding is None or self._embedding.backing_off:
            return None
        task = asyncio.create_task(self._embed_query(text))
        self._pending[text] = task
        return task

    async def _embed_query(self, text: str) -> None:
        """算一条查询向量（单位化后缓存）；失败的 warning 由嵌入客户端在登记退避时记，这里只记 debug。"""
        embedding = self._embedding
        if embedding is None:
            self._pending.pop(text, None)
            return
        try:
            (vector,) = await embedding.embed([text])
        except EmbeddingError as exc:
            logger.debug(f"查询向量算不出来，这一次只走关键词：{exc}")
            return
        finally:
            self._pending.pop(text, None)
        array = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(array))
        self._query_vectors[text] = array / norm if norm else array
        while len(self._query_vectors) > QUERY_VECTOR_CACHE_SIZE:
            self._query_vectors.popitem(last=False)


__all__ = [
    "CANDIDATES_PER_PATH",
    "RECENCY_MAX_BONUS",
    "RRF_K",
    "MemorySearch",
    "SearchHit",
    "SearchResult",
]
