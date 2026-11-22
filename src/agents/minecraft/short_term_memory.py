"""Minecraft 本场短期动态记忆（纯内存级，开播/下播清空，绝不将玩家公屏写入跨场数据库）。

负责在本场游玩期间收录 3 类高信号短文本，并支持“中文两字切分关键词 + 可选向量余弦”混合召回：
1. 玩家公屏/私聊（``player_chat``）：有界滚动保存（默认上限 200 条），仅保留本场有效对话；
2. 主动折叠块摘要与保留原话（``fold_block``）：供 ``minecraft_context_recall`` 字面未命中时同义回捞；
3. 当前存档工作文档切片（``work_doc``）：包含工作笔记段落、长期钉住规矩与本场临时规矩，按内容指纹变化增量更新。

原始 ``observe`` 方块 JSON 或工具报错等低信号噪声不在收录之列。若构造时注入了可选嵌入客户端，
写入后在后台非阻塞计算单位向量（带文本去重、并发保护与失败降级）；未配嵌入、退避中或向量尚未算好时，
检索无缝退化为中文两字切分 + 子串关键词路径。
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import (
    AbstractSet,
    Any,
    Callable,
    Deque,
    Dict,
    FrozenSet,
    List,
    Literal,
    Optional,
    Sequence,
    Set,
    Tuple,
)

import numpy as np

from src.modules.llm.embedding import EmbeddingClient, EmbeddingError
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

__all__ = [
    "DEFAULT_CHAT_HALF_LIFE_MS",
    "DEFAULT_CHAT_STALE_PENALTY",
    "DEFAULT_CHAT_STALE_THRESHOLD_MS",
    "DEFAULT_MAX_FOLD_BLOCKS",
    "DEFAULT_MAX_PLAYER_CHATS",
    "DEFAULT_MIN_COSINE",
    "DEFAULT_STALE_CHAT_MIN_SCORE",
    "DEFAULT_WAIT_VECTOR_MS",
    "KIND_FOLD_BLOCK",
    "KIND_PLAYER_CHAT",
    "KIND_WORK_DOC",
    "SessionShortTermMemory",
    "ShortTermHit",
    "ShortTermKind",
    "format_relative_age_ms",
    "format_short_term_hit",
]

logger = get_logger("SessionShortTermMemory")

ShortTermKind = Literal["player_chat", "fold_block", "work_doc"]

KIND_PLAYER_CHAT: ShortTermKind = "player_chat"
KIND_FOLD_BLOCK: ShortTermKind = "fold_block"
KIND_WORK_DOC: ShortTermKind = "work_doc"

DEFAULT_MAX_PLAYER_CHATS = 200
DEFAULT_MAX_FOLD_BLOCKS = 80
DEFAULT_MIN_COSINE = 0.48
DEFAULT_WAIT_VECTOR_MS = 1_200
# 公屏记忆时间衰减：半衰期 20 分钟；超过 45 分钟的孤立短闲聊大幅降权并低于门槛直接过滤，防止陈旧闪回
DEFAULT_CHAT_HALF_LIFE_MS = 20 * 60 * 1_000
DEFAULT_CHAT_STALE_THRESHOLD_MS = 45 * 60 * 1_000
DEFAULT_CHAT_STALE_PENALTY = 0.25
DEFAULT_STALE_CHAT_MIN_SCORE = 0.15
_STALE_SHORT_CHAT_MAX_CHARS = 24
_FOLD_DECAY_FLOOR = 0.35
_QUERY_VECTOR_CACHE_SIZE = 64
_TEXT_VECTOR_CACHE_SIZE = 512
_MAX_CHAT_CONTENT_CHARS = 400
_MAX_EMBED_CONCURRENCY = 2
_EXCLUDE_CONTAINS_MIN_LEN = 6

# 中文汉字、日文假名与韩文音节：连写不带空格，切为重叠两字词
_CJK_CHARS = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\U00020000-\U0003134f"
_CJK_RUN = re.compile(f"[{_CJK_CHARS}]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
_BRACKET_TITLE_RE = re.compile(r"^【([^】]{1,24})】\s*$")
_BULLET_PREFIX_RE = re.compile(r"^[-*•+]\s+|^\d+[.)、]\s+")

# 查询时跳过的高频虚词，避免“什么”“那个”把无关短文本拉进前排
_STOPWORDS: FrozenSet[str] = frozenset(
    (
        "什么 怎么 怎样 哪里 哪儿 哪个 哪些 我们 你们 他们 大家 自己 那个 这个 刚才 之前 以前 现在 "
        "一下 一点 一些 觉得 记得 好像 应该 其实 就是 还是 因为 所以 如果 但是 不过 然后 已经 正在 一直 "
        "的 了 吗 呢 吧 啊 呀 啦 喔 哦 噢 哈 嘿 着 过 我 你 他 她 它 在 是 有 就 都 也 还 又 "
        "the a an of to in on at by and or is are was were be do did what how where when why"
    ).split()
)


def _normalize_text(text: str) -> str:
    """统一大小写与全角，仅保留字母、数字与汉字，便于字面匹配与近端原文去重。"""
    folded = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(ch for ch in folded if ch.isalnum())


def _bigram_tokens(text: str) -> List[str]:
    """中文两字切分：连续汉字切重叠双字，单字保留，英文数字段按原词保留。"""
    folded = unicodedata.normalize("NFKC", text or "").lower()
    runs: List[str] = []
    buf: List[str] = []
    for ch in folded:
        if ch.isalnum():
            buf.append(ch)
        elif buf:
            runs.append("".join(buf))
            buf = []
    if buf:
        runs.append("".join(buf))

    tokens: List[str] = []
    for run in runs:
        cursor = 0
        for match in _CJK_RUN.finditer(run):
            if match.start() > cursor:
                tokens.append(run[cursor : match.start()])
            part = match.group()
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[idx : idx + 2] for idx in range(len(part) - 1))
            cursor = match.end()
        if cursor < len(run):
            tokens.append(run[cursor:])
    return tokens


def _query_terms(query: str) -> Tuple[List[str], List[str]]:
    """提取查询实词二元组与空白切分后的归一化关键词（全虚词时自动回退原词表）。"""
    raw_bigrams = list(dict.fromkeys(_bigram_tokens(query)))
    filtered = [tok for tok in raw_bigrams if tok not in _STOPWORDS]
    terms = filtered if filtered else raw_bigrams

    words = [_normalize_text(part) for part in (query or "").split()]
    non_empty_words = [word for word in dict.fromkeys(words) if word]
    real_words = [word for word in non_empty_words if word not in _STOPWORDS]
    keywords = real_words if real_words else non_empty_words
    return terms, keywords


def _looks_like_raw_json_blob(text: str) -> bool:
    """拦截误传入的原始工具回执或大块 JSON 结构，守住高信号短文本边界。"""
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    return (stripped.startswith("{") and stripped.endswith("}") and '"' in stripped and ":" in stripped) or (
        stripped.startswith("[{") and stripped.endswith("}]")
    )


def _split_notebook_segments(notebook: str) -> List[str]:
    """把工作笔记按小节标题与非空行切分为语义自足的短句，保留所属小节名。"""
    segments: List[str] = []
    section = ""
    for raw_line in (notebook or "").splitlines():
        stripped = raw_line.strip()
        if not stripped or set(stripped) <= {"-", "=", "*", "_"}:
            continue
        if heading := _HEADING_RE.match(stripped):
            section = heading.group(2).strip()
        elif bracket := _BRACKET_TITLE_RE.match(stripped):
            section = bracket.group(1).strip()
        elif cleaned := _BULLET_PREFIX_RE.sub("", stripped).strip():
            has_sec = not section or cleaned.startswith(f"【{section}】") or section in cleaned[: len(section) + 4]
            segments.append(cleaned if has_sec else f"【{section}】{cleaned}")
    return segments


def _normalize_lines(lines: Sequence[str]) -> Tuple[str, ...]:
    """清理字符串序列中的多余空白、空项与重复项（保持原顺序）。"""
    cleaned: List[str] = []
    seen: Set[str] = set()
    for item in lines or ():
        if not isinstance(item, str):
            continue
        line = " ".join(item.split())
        if not line or line in seen:
            continue
        seen.add(line)
        cleaned.append(line)
    return tuple(cleaned)


def _unit_vector(vector: Sequence[float]) -> Optional[np.ndarray]:
    """转为 L2 单位化 float32 向量；空向量或零向量返回 None。"""
    array = np.asarray(vector, dtype=np.float32)
    if array.ndim != 1 or array.size == 0:
        return None
    norm = float(np.linalg.norm(array))
    if norm <= 0.0:
        return None
    unit = (array / norm).astype(np.float32, copy=False)
    unit.setflags(write=False)
    return unit


def format_relative_age_ms(age_ms: int) -> str:
    """将毫秒时间差渲染为中文相对时长标签（如“刚刚”“12分钟前”“1小时37分钟前”）。"""
    safe_ms = max(0, int(age_ms))
    if safe_ms < 60_000:
        return "刚刚"
    total_minutes = safe_ms // 60_000
    if total_minutes < 60:
        return f"{total_minutes}分钟前"
    hours, rem_minutes = divmod(total_minutes, 60)
    if rem_minutes > 0:
        return f"{hours}小时{rem_minutes}分钟前"
    return f"{hours}小时前"


def format_short_term_hit(hit: "ShortTermHit", *, now_ms: Optional[int] = None) -> str:
    """为短期记忆命中结果强制附带相对时间前缀，供 ``[脑中闪过]`` 清晰标明记忆新旧。"""
    ref_ms = int(now_ms) if now_ms is not None else 0
    if hit.timestamp_ms > 0 and ref_ms >= hit.timestamp_ms:
        age = format_relative_age_ms(ref_ms - hit.timestamp_ms)
    else:
        age = "刚刚"
    return f"[{age}] {hit.text}"


@dataclass(frozen=True)
class ShortTermHit:
    """本场短期动态记忆的一条召回命中结果。"""

    entry_id: str
    kind: ShortTermKind
    text: str
    score: float
    timestamp_ms: int
    sender: str = ""
    private: bool = False
    is_self: bool = False
    block_id: str = ""
    span_label: str = ""
    doc_section: str = ""
    keyword_score: float = 0.0
    vector_score: Optional[float] = None


@dataclass
class _ShortTermEntry:
    """内部记忆条目：持有展示文本、参与匹配的索引文本、词项集合与可选单位向量。"""

    entry_id: str
    kind: ShortTermKind
    text: str
    search_text: str
    norm_text: str
    tokens: FrozenSet[str]
    timestamp_ms: int
    sender: str = ""
    private: bool = False
    is_self: bool = False
    raw_content_len: int = 0
    block_id: str = ""
    span_label: str = ""
    summary: str = ""
    preserved_lines: Tuple[str, ...] = ()
    doc_section: str = ""
    vector: Optional[np.ndarray] = None


class SessionShortTermMemory:
    """本场游玩期内存级动态记忆：收纳公屏、折叠块与工作文档切片，支持关键词 + 向量混合召回。"""

    def __init__(
        self,
        *,
        embedding_client: Optional[EmbeddingClient] = None,
        clock: Callable[[], int] = now_ms,
        max_player_chats: int = DEFAULT_MAX_PLAYER_CHATS,
        max_fold_blocks: int = DEFAULT_MAX_FOLD_BLOCKS,
        min_cosine: float = DEFAULT_MIN_COSINE,
        wait_vector_ms: int = DEFAULT_WAIT_VECTOR_MS,
        chat_half_life_ms: int = DEFAULT_CHAT_HALF_LIFE_MS,
        chat_stale_threshold_ms: int = DEFAULT_CHAT_STALE_THRESHOLD_MS,
        chat_stale_penalty: float = DEFAULT_CHAT_STALE_PENALTY,
        stale_chat_min_score: float = DEFAULT_STALE_CHAT_MIN_SCORE,
    ) -> None:
        self._embedding = embedding_client
        self._clock = clock
        self._max_player_chats = max(1, int(max_player_chats))
        self._max_fold_blocks = max(1, int(max_fold_blocks))
        self._min_cosine = float(min_cosine)
        self._wait_vector_ms = max(0, int(wait_vector_ms))
        self._chat_half_life_ms = max(0, int(chat_half_life_ms))
        self._chat_stale_threshold_ms = max(0, int(chat_stale_threshold_ms))
        self._chat_stale_penalty = max(0.0, min(1.0, float(chat_stale_penalty)))
        self._stale_chat_min_score = max(0.0, float(stale_chat_min_score))

        # 场次代际号：开播/下播 clear_session 时递增，防止上一场在途异步嵌入任务回写污染新场次
        self._epoch = 0
        self._next_seq = 1
        self._player_chats: Deque[_ShortTermEntry] = deque(maxlen=self._max_player_chats)
        self._fold_blocks: "OrderedDict[str, _ShortTermEntry]" = OrderedDict()
        self._work_docs: List[_ShortTermEntry] = []
        self._work_docs_signature: Optional[Tuple[Tuple[str, str], ...]] = None

        # 文本 → 单位向量缓存与在途去重表：避免同文本并发重算，也支持后台任务并发上限保护
        self._text_vectors: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._query_vectors: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._pending_texts: Set[str] = set()
        self._pending_query_tasks: Dict[str, "asyncio.Task[None]"] = {}
        self._bg_tasks: Set["asyncio.Task[None]"] = set()
        self._embed_sem = asyncio.Semaphore(_MAX_EMBED_CONCURRENCY)

    def clear_session(self) -> None:
        """开播/下播清空全部本场短期记忆与在途后台嵌入任务，绝不遗留跨场数据。"""
        self._epoch += 1
        for task in list(self._bg_tasks):
            if not task.done():
                task.cancel()
        self._bg_tasks.clear()
        self._pending_texts.clear()
        self._pending_query_tasks.clear()
        self._player_chats.clear()
        self._fold_blocks.clear()
        self._work_docs.clear()
        self._work_docs_signature = None
        self._text_vectors.clear()
        self._query_vectors.clear()

    def entry_counts(self) -> Dict[str, int]:
        """只读返回当前 3 类短期记忆的条数统计（供诊断与单测核查）。"""
        return {
            KIND_PLAYER_CHAT: len(self._player_chats),
            KIND_FOLD_BLOCK: len(self._fold_blocks),
            KIND_WORK_DOC: len(self._work_docs),
        }

    def record_player_chat(
        self,
        sender: str,
        content: str,
        *,
        private: bool = False,
        timestamp_ms: int = 0,
    ) -> Optional[str]:
        """收录一条玩家公屏或私聊短文本（纯内存有界保存，拒收空白与原始 JSON 噪声）。"""
        cleaned = " ".join((content or "").split())
        if not cleaned or _looks_like_raw_json_blob(cleaned):
            return None
        if len(cleaned) > _MAX_CHAT_CONTENT_CHARS:
            cleaned = f"{cleaned[: _MAX_CHAT_CONTENT_CHARS - 1]}…"
        speaker = (sender or "").strip()
        ts = int(timestamp_ms) if timestamp_ms > 0 else int(self._clock())
        prefix = "[私聊] " if private else ""
        display = f"{prefix}{speaker}：{cleaned}" if speaker else f"{prefix}{cleaned}"

        # 紧邻完全重复的刷屏回声直接刷新时间戳，不占多条名额
        if self._player_chats and self._player_chats[-1].text == display:
            self._player_chats[-1].timestamp_ms = ts
            return self._player_chats[-1].entry_id

        entry_id = self._take_id("chat")
        entry = self._build_entry(
            entry_id=entry_id,
            kind=KIND_PLAYER_CHAT,
            text=display,
            search_text=display,
            timestamp_ms=ts,
            sender=speaker,
            private=private,
            is_self=False,
            raw_content_len=len(cleaned),
        )
        self._player_chats.append(entry)
        self._schedule_entry_embedding(entry)
        return entry_id

    def record_self_chat(self, text: str, timestamp_ms: int = 0) -> Optional[str]:
        """收录一条己方发出的游戏公屏句（不涉及隐私，正常走短期索引与向量嵌入，构成完整双向对话记忆）。"""
        cleaned = " ".join((text or "").split())
        if not cleaned or _looks_like_raw_json_blob(cleaned):
            return None
        if len(cleaned) > _MAX_CHAT_CONTENT_CHARS:
            cleaned = f"{cleaned[: _MAX_CHAT_CONTENT_CHARS - 1]}…"
        ts = int(timestamp_ms) if timestamp_ms > 0 else int(self._clock())
        display = f"你回过：{cleaned}"

        if self._player_chats and self._player_chats[-1].text == display and self._player_chats[-1].is_self:
            self._player_chats[-1].timestamp_ms = ts
            return self._player_chats[-1].entry_id

        entry_id = self._take_id("self_chat")
        entry = self._build_entry(
            entry_id=entry_id,
            kind=KIND_PLAYER_CHAT,
            text=display,
            search_text=display,
            timestamp_ms=ts,
            sender="你",
            private=False,
            is_self=True,
            raw_content_len=len(cleaned),
        )
        self._player_chats.append(entry)
        self._schedule_entry_embedding(entry)
        return entry_id

    def format_hit_for_flash(self, hit: ShortTermHit, *, now_ms: Optional[int] = None) -> str:
        """把短期记忆命中项格式化为带相对时间前缀的 ``[脑中闪过]`` 单行文本。"""
        ref_ms = int(self._clock()) if now_ms is None else int(now_ms)
        return format_short_term_hit(hit, now_ms=ref_ms)

    def record_fold_block(
        self,
        block_id: str,
        span_label: str,
        summary: str,
        preserved_lines: Sequence[str] = (),
        *,
        private: bool = False,
        timestamp_ms: int = 0,
    ) -> Optional[str]:
        """收录一段主动折叠块的摘要与保留原话，供后续字面未命中时的同义语义回捞。

        含私聊信息的折叠块（显式 ``private=True`` 或内容出现 ``[公屏·私聊]``/``[私聊]`` 标记）
        会强制标为 ``private=True``，绝不外发外部嵌入 API，且召回结果透传私聊标记供上游隔离。
        """
        bid = (block_id or "").strip()
        clean_summary = (summary or "").strip()
        clean_preserved = _normalize_lines(preserved_lines)
        if not bid or (not clean_summary and not clean_preserved):
            return None
        ts = int(timestamp_ms) if timestamp_ms > 0 else int(self._clock())
        span = (span_label or "").strip() or bid
        is_private = bool(private) or _contains_private_marker(clean_summary, clean_preserved)

        if clean_preserved:
            preserved_joined = "；".join(clean_preserved)
            search_text = f"{clean_summary}\n{preserved_joined}" if clean_summary else preserved_joined
            display = (
                f"[{bid} · {span}] {clean_summary}（保留原话：{preserved_joined}）"
                if clean_summary
                else f"[{bid} · {span}] {preserved_joined}"
            )
        else:
            search_text = clean_summary
            display = f"[{bid} · {span}] {clean_summary}"

        # 同一块号若正文与私聊标记均未变化则仅更新时间戳，不重算向量；若升级为私聊则立即清除已有向量
        existing = self._fold_blocks.get(bid)
        if existing is not None and existing.search_text == search_text and existing.private == is_private:
            existing.timestamp_ms = ts
            self._fold_blocks.move_to_end(bid)
            return existing.entry_id

        entry_id = f"fold:{bid}"
        entry = self._build_entry(
            entry_id=entry_id,
            kind=KIND_FOLD_BLOCK,
            text=display,
            search_text=search_text,
            timestamp_ms=ts,
            private=is_private,
            block_id=bid,
            span_label=span,
            summary=clean_summary,
            preserved_lines=clean_preserved,
        )
        self._fold_blocks[bid] = entry
        self._fold_blocks.move_to_end(bid)
        while len(self._fold_blocks) > self._max_fold_blocks:
            self._fold_blocks.popitem(last=False)
        self._schedule_entry_embedding(entry)
        return entry_id

    def sync_work_docs(
        self,
        notebook: str,
        pinned_rules: Sequence[str],
        session_rules: Sequence[str],
        *,
        timestamp_ms: int = 0,
    ) -> int:
        """同步当前存档工作笔记、长期钉住规矩与本场临时规矩；仅内容变化时更新并增量算向量。

        返回本次新增或发生变化的切片条数；内容与上次完全一致时直接返回 ``0``，零额外开销。
        """
        raw_items: List[Tuple[str, str]] = []
        for seg in _split_notebook_segments(notebook):
            raw_items.append(("notebook", seg))
        for rule in _normalize_lines(pinned_rules):
            raw_items.append(("pinned_rule", rule))
        for rule in _normalize_lines(session_rules):
            raw_items.append(("session_rule", rule))

        signature = tuple(raw_items)
        if signature == self._work_docs_signature:
            return 0

        ts = int(timestamp_ms) if timestamp_ms > 0 else int(self._clock())
        # 复用此前已同步且内容未变的条目（连同算好的向量），仅对新增或修改的切片触发后台向量计算
        existing_map: Dict[Tuple[str, str], _ShortTermEntry] = {
            (item.doc_section, item.search_text): item for item in self._work_docs
        }
        updated: List[_ShortTermEntry] = []
        changed = 0
        for section, text in raw_items:
            if _looks_like_raw_json_blob(text):
                continue
            key = (section, text)
            reused = existing_map.get(key)
            if reused is not None:
                updated.append(reused)
                continue
            label = {"notebook": "工作笔记", "pinned_rule": "长期规矩", "session_rule": "本场规矩"}.get(section, "文档")
            entry = self._build_entry(
                entry_id=self._take_id("work"),
                kind=KIND_WORK_DOC,
                text=f"[{label}] {text}",
                search_text=text,
                timestamp_ms=ts,
                doc_section=section,
            )
            updated.append(entry)
            changed += 1
            self._schedule_entry_embedding(entry)

        self._work_docs = updated
        self._work_docs_signature = signature
        return changed

    async def flush_embeddings(self) -> None:
        """等待所有在途后台嵌入任务完成（同时补算同步期尚未启动的非私聊条目，便于单测与停机收口）。"""
        if self._embedding is not None:
            for entry in self._iter_entries(None):
                if not entry.private and entry.vector is None:
                    self._schedule_entry_embedding(entry)
        while self._bg_tasks:
            pending = list(self._bg_tasks)
            await asyncio.gather(*pending, return_exceptions=True)

    async def search_fold_blocks(self, query: str, *, limit: int = 3) -> List[Dict[str, Any]]:
        """语义/关键词回捞折叠块摘要与保留原话，供 ``minecraft_context_recall`` 字面未命中时兜底。"""
        hits = await self.search_entries(query, kinds={KIND_FOLD_BLOCK}, limit=limit)
        results: List[Dict[str, Any]] = []
        for hit in hits:
            entry = self._fold_blocks.get(hit.block_id)
            if entry is None:
                continue
            results.append(
                {
                    "block": entry.block_id,
                    "range": entry.span_label,
                    "summary": entry.summary,
                    "preserved_lines": list(entry.preserved_lines),
                    "score": round(hit.score, 4),
                    "vector_score": round(hit.vector_score, 4) if hit.vector_score is not None else None,
                }
            )
        return results

    async def search_entries(
        self,
        query: str,
        *,
        kinds: Optional[AbstractSet[str]] = None,
        exclude_texts: AbstractSet[str] = frozenset(),
        limit: int = 6,
        wait_vector_ms: Optional[int] = None,
        now_ms: Optional[int] = None,
        min_score: float = 0.0,
    ) -> List[ShortTermHit]:
        """结合中文两字切分关键词、可选向量余弦与时间衰减检索本场短期记忆，支持按类别筛选与近端原文去重。

        ``wait_vector_ms=0`` 用于被动快路（如 ``[脑中闪过]``）：查询向量已缓存时立即融合余弦；
        未就绪则在后台非阻塞起算留给下一次，本次毫秒级直接返回关键词命中，不被慢嵌入拖死或取消。
        ``wait_vector_ms=None`` 时沿用构造默认等待上限（供主动回想工具使用）。
        """
        clean_query = (query or "").strip()
        if not clean_query or limit <= 0:
            return []

        candidates = list(self._iter_entries(kinds))
        if not candidates:
            return []

        # 同步期写入的非私聊条目在异步查询时顺手挂上后台算向量；私聊条目严禁外发嵌入服务
        if self._embedding is not None:
            for entry in candidates:
                if entry.private:
                    continue
                if entry.vector is None and entry.search_text in self._text_vectors:
                    entry.vector = self._text_vectors[entry.search_text]
                elif entry.vector is None:
                    self._schedule_entry_embedding(entry)

        terms, keywords = _query_terms(clean_query)
        term_set = set(terms)
        exclude_norms = [norm for raw in exclude_texts if (norm := _normalize_text(raw))]
        effective_wait_ms = self._wait_vector_ms if wait_vector_ms is None else max(0, int(wait_vector_ms))
        has_embeddable_candidates = any(not entry.private for entry in candidates)
        query_vec = (
            await self._get_query_vector(clean_query, wait_ms=effective_wait_ms) if has_embeddable_candidates else None
        )

        # 一次性用 numpy 矩阵计算所有已有向量候选的余弦相似度
        vector_scores: Dict[int, float] = {}
        if query_vec is not None:
            vec_indices: List[int] = []
            vec_rows: List[np.ndarray] = []
            for idx, entry in enumerate(candidates):
                if entry.vector is not None and entry.vector.shape[0] == query_vec.shape[0]:
                    vec_indices.append(idx)
                    vec_rows.append(entry.vector)
            if vec_rows:
                matrix = np.stack(vec_rows).astype(np.float32, copy=False)
                cosines = matrix @ query_vec
                for pos, entry_idx in enumerate(vec_indices):
                    cos = float(cosines[pos])
                    if cos >= self._min_cosine:
                        vector_scores[entry_idx] = cos

        ref_ms = int(self._clock()) if now_ms is None else int(now_ms)
        scored: List[Tuple[float, int, ShortTermHit]] = []
        max_ts = max((entry.timestamp_ms for entry in candidates), default=0)
        for idx, entry in enumerate(candidates):
            if _is_excluded_text(entry.norm_text, exclude_norms):
                continue
            kw_score = _keyword_match_score(entry.norm_text, entry.tokens, term_set, keywords)
            vec_score = vector_scores.get(idx)
            if kw_score <= 0.0 and vec_score is None:
                continue
            raw_score = kw_score + (vec_score or 0.0)
            age_ms = (
                max(0, ref_ms - entry.timestamp_ms) if entry.timestamp_ms > 0 and ref_ms >= entry.timestamp_ms else 0
            )
            decay = self._time_decay_factor(entry, age_ms)
            decayed_score = raw_score * decay
            # 超过陈旧门槛（默认 45 分钟）的旧公屏若衰减后仍低于门槛，直接过滤杜绝远古闲聊闪回
            if (
                entry.kind == KIND_PLAYER_CHAT
                and self._chat_stale_threshold_ms > 0
                and age_ms >= self._chat_stale_threshold_ms
                and decayed_score < self._stale_chat_min_score
            ):
                continue
            if min_score > 0.0 and decayed_score < min_score:
                continue
            # 加极微幅新近度偏置打破同分平手（不跨档反超高相关记录）
            recency_tie = 1e-4 * (entry.timestamp_ms / max_ts) if max_ts > 0 and entry.timestamp_ms > 0 else 0.0
            total = decayed_score + recency_tie
            scored.append(
                (
                    total,
                    entry.timestamp_ms,
                    ShortTermHit(
                        entry_id=entry.entry_id,
                        kind=entry.kind,
                        text=entry.text,
                        score=total,
                        timestamp_ms=entry.timestamp_ms,
                        sender=entry.sender,
                        private=entry.private,
                        is_self=entry.is_self,
                        block_id=entry.block_id,
                        span_label=entry.span_label,
                        doc_section=entry.doc_section,
                        keyword_score=kw_score,
                        vector_score=vec_score,
                    ),
                )
            )

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:limit]]

    def _time_decay_factor(self, entry: _ShortTermEntry, age_ms: int) -> float:
        """按条目类型与距今时长计算时间衰减因子：
        - 工作笔记与规矩（work_doc）：持续生效不衰减（1.0）；
        - 折叠块（fold_block）：温和指数衰减（半衰期 60 分钟，保底 0.35），兼顾近段优先与长场次主动回捞；
        - 玩家与己方公屏（player_chat）：指数衰减（默认半衰期 20 分钟），超过 45 分钟的孤立短闲聊叠加大幅降权。
        """
        if entry.kind == KIND_WORK_DOC or age_ms <= 0 or self._chat_half_life_ms <= 0:
            return 1.0
        if entry.kind == KIND_FOLD_BLOCK:
            fold_half_life = self._chat_half_life_ms * 3
            return max(_FOLD_DECAY_FLOOR, 0.5 ** (age_ms / fold_half_life))
        # 公屏对话：按 20 分钟半衰期指数衰减
        decay = 0.5 ** (age_ms / self._chat_half_life_ms)
        if self._chat_stale_threshold_ms > 0 and age_ms >= self._chat_stale_threshold_ms:
            # 超过 45 分钟的孤立短闲聊额外降权，彻底避免仅凭发信人名字高分满血闪回
            if entry.raw_content_len <= _STALE_SHORT_CHAT_MAX_CHARS:
                decay *= self._chat_stale_penalty
        return decay

    def _take_id(self, prefix: str) -> str:
        seq = self._next_seq
        self._next_seq += 1
        return f"{prefix}:{seq}"

    def _build_entry(
        self,
        *,
        entry_id: str,
        kind: ShortTermKind,
        text: str,
        search_text: str,
        timestamp_ms: int,
        sender: str = "",
        private: bool = False,
        is_self: bool = False,
        raw_content_len: int = 0,
        block_id: str = "",
        span_label: str = "",
        summary: str = "",
        preserved_lines: Tuple[str, ...] = (),
        doc_section: str = "",
    ) -> _ShortTermEntry:
        cached_vec = None if private else self._text_vectors.get(search_text)
        return _ShortTermEntry(
            entry_id=entry_id,
            kind=kind,
            text=text,
            search_text=search_text,
            norm_text=_normalize_text(search_text),
            tokens=frozenset(_bigram_tokens(search_text)),
            timestamp_ms=timestamp_ms,
            sender=sender,
            private=private,
            is_self=is_self,
            raw_content_len=raw_content_len if raw_content_len > 0 else len(search_text),
            block_id=block_id,
            span_label=span_label,
            summary=summary,
            preserved_lines=preserved_lines,
            doc_section=doc_section,
            vector=cached_vec,
        )

    def _iter_entries(self, kinds: Optional[AbstractSet[str]]) -> List[_ShortTermEntry]:
        entries: List[_ShortTermEntry] = []
        if kinds is None or KIND_PLAYER_CHAT in kinds:
            entries.extend(self._player_chats)
        if kinds is None or KIND_FOLD_BLOCK in kinds:
            entries.extend(self._fold_blocks.values())
        if kinds is None or KIND_WORK_DOC in kinds:
            entries.extend(self._work_docs)
        return entries

    def _schedule_entry_embedding(self, entry: _ShortTermEntry) -> None:
        """在有运行中事件循环且配了嵌入客户端时，后台非阻塞计算非私聊条目向量（按文本去重防并发风暴）。"""
        if self._embedding is None or entry.private or entry.vector is not None:
            return
        text = entry.search_text
        if not text.strip():
            return
        cached = self._text_vectors.get(text)
        if cached is not None:
            self._text_vectors.move_to_end(text)
            entry.vector = cached
            return
        if text in self._pending_texts or self._embedding.backing_off:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 纯同步上下文调用 record_* 时不报错，留待下次异步查询或 flush 时顺手补算
            return
        self._pending_texts.add(text)
        epoch = self._epoch
        task = loop.create_task(self._compute_text_embedding(text, epoch))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _compute_text_embedding(self, text: str, epoch: int) -> None:
        """后台算一条非私聊文本的单位向量并回填同文本公开条目；失败记 warning 降级，绝不抛异常。"""
        embedding = self._embedding
        try:
            if embedding is None or epoch != self._epoch:
                return
            async with self._embed_sem:
                if epoch != self._epoch or embedding.backing_off:
                    return
                (raw_vec,) = await embedding.embed([text])
        except (EmbeddingError, Exception) as exc:  # noqa: BLE001 - 短期记忆嵌入属增强通道，失败降级回关键词
            logger.warning(f"本场短期记忆后台计算向量失败，已降级关键词检索: {type(exc).__name__}: {exc}", exc=True)
            return
        finally:
            self._pending_texts.discard(text)

        if epoch != self._epoch:
            return
        unit = _unit_vector(raw_vec)
        if unit is None:
            return
        self._text_vectors[text] = unit
        self._text_vectors.move_to_end(text)
        while len(self._text_vectors) > _TEXT_VECTOR_CACHE_SIZE:
            self._text_vectors.popitem(last=False)
        for entry in self._iter_entries(None):
            if not entry.private and entry.search_text == text:
                entry.vector = unit

    def _ensure_query_embedding_task(self, query: str) -> Optional["asyncio.Task[None]"]:
        """同一查询文本在后台只算一次向量；嵌入服务退避时不新开任务。"""
        existing = self._pending_query_tasks.get(query)
        if existing is not None and not existing.done():
            return existing
        if self._embedding is None or self._embedding.backing_off:
            return None
        loop = asyncio.get_running_loop()
        epoch = self._epoch
        task = loop.create_task(self._compute_query_embedding(query, epoch))
        self._pending_query_tasks[query] = task
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def _compute_query_embedding(self, query: str, epoch: int) -> None:
        """后台算查询向量并入 LRU 缓存；不因单次搜索超时而被取消，算好后留给后续查询命中。"""
        embedding = self._embedding
        try:
            if embedding is None or epoch != self._epoch or embedding.backing_off:
                return
            (raw_vec,) = await embedding.embed([query])
        except (EmbeddingError, Exception) as exc:  # noqa: BLE001 - 嵌入异常不中断短期记忆检索
            logger.warning(f"本场短期记忆查询向量计算失败，本次退化关键词检索: {type(exc).__name__}: {exc}", exc=True)
            return
        finally:
            self._pending_query_tasks.pop(query, None)

        if epoch != self._epoch:
            return
        unit = _unit_vector(raw_vec)
        if unit is None:
            return
        self._query_vectors[query] = unit
        self._query_vectors.move_to_end(query)
        while len(self._query_vectors) > _QUERY_VECTOR_CACHE_SIZE:
            self._query_vectors.popitem(last=False)

    async def _get_query_vector(self, query: str, *, wait_ms: int) -> Optional[np.ndarray]:
        """取查询向量：命中缓存立即返回；未命中时在后台起算，``wait_ms=0`` 零等待直接走关键词。"""
        if self._embedding is None:
            return None
        cached = self._query_vectors.get(query)
        if cached is not None:
            self._query_vectors.move_to_end(query)
            return cached
        task = self._ensure_query_embedding_task(query)
        if task is None or wait_ms <= 0:
            return None
        done, _pending = await asyncio.wait({task}, timeout=wait_ms / 1000)
        if not done:
            logger.warning(f"本场短期记忆查询向量等待超时（{wait_ms}ms），本次退化关键词检索并留待后台入缓存")
            return None
        return self._query_vectors.get(query)


def _contains_private_marker(summary: str, preserved_lines: Sequence[str]) -> bool:
    """兜底识别折叠摘要或保留原话中是否夹带私聊标签，防止调用方漏标导致私聊外泄。"""
    markers = ("[公屏·私聊]", "[私聊]")
    if any(marker in summary for marker in markers):
        return True
    return any(marker in line for line in preserved_lines for marker in markers)


def _keyword_match_score(
    norm_text: str,
    entry_tokens: FrozenSet[str],
    query_terms: Set[str],
    keywords: Sequence[str],
) -> float:
    """结合中文两字切分重合度与字面子串命中率计算关键词得分（0 表示未命中）。"""
    if not norm_text:
        return 0.0
    kw_hits = sum(1 for kw in keywords if kw and kw in norm_text)
    tok_hits = len(entry_tokens & query_terms) if query_terms else 0
    if kw_hits == 0 and tok_hits == 0:
        return 0.0
    # 多词查询仅擦到孤立 1 个双字碎片且整词一个都没中时，判定为噪音不放行
    if kw_hits == 0 and len(query_terms) >= 3 and tok_hits <= 1:
        return 0.0
    kw_ratio = kw_hits / max(1, len(keywords))
    tok_ratio = tok_hits / max(1, len(query_terms))
    return 0.65 * kw_ratio + 0.35 * tok_ratio


def _is_excluded_text(entry_norm: str, exclude_norms: Sequence[str]) -> bool:
    """过滤已在近端上下文展示过的原文（完全相等，或较长句子互为包含）。"""
    if not entry_norm or not exclude_norms:
        return False
    for excluded in exclude_norms:
        if entry_norm == excluded:
            return True
        if len(entry_norm) >= _EXCLUDE_CONTAINS_MIN_LEN and len(excluded) >= _EXCLUDE_CONTAINS_MIN_LEN:
            if entry_norm in excluded or excluded in entry_norm:
                return True
    return False
