"""LongMemEval 跨场召回与副作用评测：走生产检索链路，在临时库上量召回率与误召回/重复占坑。

跑的是生产代码，不是近似模拟：
- 写入用生产的 ``SessionRepo.insert_live_session`` / ``ChatRepo.insert_live_chat``
  / ``ArchiveRepo.insert_line``；
- 索引用生产的 ``MemoryIndexer``（循环补算至 ``pending_total == 0``，不漏大库后半截）；
- 检索用生产的 ``MemorySearch`` 与 ``MemoryRecallService.recall_detailed``（直接核对回想工具
  真实选出的前 8 条，不从外部猜候选池）。

库建在临时目录，跑完即弃，不碰任何真实库。
语义一路要嵌入服务；没配就只走关键词一路（结果里会带"只按字面查"的提示）。

注意区分官方数据集档位与库隔离方式：
- ``longmemeval_oracle.json`` 已剥离无关闲聊场次（每题中位仅约 23 轮），在 ``per_item`` 下前 8 条
  占全库三分之一以上，只能看无干扰上限；``longmemeval_s`` 每题含约 40 场干扰对话，才是单题带干扰检索。
- ``shared`` 模式先把全部题目的语料灌完并建满索引，再统一跑全部查询（修复"边灌边查"导致前松后紧的偏差），
  约 1.1 万轮的规模接近主播播满二三十场（约一个月）后的真实跨场记忆库大小。

正向指标与副作用指标成对输出：
- 正向召回：口径 A（前 8 条含答案轮）、口径 B（第 1 条是答案轮）；另附口径 C（结果文本含金标准答案字符串，
  仅作措辞复现率对照，不代表检索能力）；
- 副作用：同源重复占坑率（同一轮台词在单次结果里重复占掉的名额占比）、无答案题非空率与平均误塞条数、
  有答案题平均伴随无关条数。

用法：
    uv run python scripts/memory_eval/longmemeval.py --dataset docs/tmp/longmemeval/oracle.json
    uv run python scripts/memory_eval/longmemeval.py --dataset ... --shape speech_plus_archive --isolation shared
    uv run python scripts/memory_eval/longmemeval.py --selftest
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import statistics
import sys
import tempfile
import time
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        _reconfigure(encoding="utf-8", errors="replace")

from src.agents.streamer.memory.index import MemoryIndexer  # noqa: E402
from src.agents.streamer.memory.recall import (  # noqa: E402
    MAX_RESULTS,
    SCOPE_ALL,
    MemoryRecallService,
    RecallOutcome,
)
from src.agents.streamer.memory.search import MemorySearch  # noqa: E402
from src.modules.config.model_schemas import EmbeddingConfig, LLMProviderConfig  # noqa: E402
from src.modules.llm.embedding import EmbeddingClient, EmbeddingError, build_embedding_client  # noqa: E402
from src.modules.logging import get_logger  # noqa: E402
from src.modules.storage.database import SQLiteDatabase  # noqa: E402

logger = get_logger("LongMemEval")

#: 语料起始毫秒时间戳（逐场递增，场次时间互不重叠）
BASE_START_MS = 1_700_000_000_000
#: 相邻两个场次相隔多久（毫秒）
SESSION_GAP_MS = 60 * 60 * 1000
#: 同一场里相邻两轮相隔多久（毫秒）
TURN_GAP_MS = 30 * 1000
#: 索引与查询时钟比最后一条台词晚多久（毫秒，须满 30 秒才进全文与向量索引）
INDEX_SETTLE_LAG_MS = 60 * 1000
#: 档案行标题取原文前多少字
ARCHIVE_TITLE_CHARS = 80
#: 送往远端嵌入接口的单段最大字符数（防超长文本超过 bge-m3 8192 token 上限触发 HTTP 400）
MAX_EMBED_INPUT_CHARS = 6_000
#: 向量预热默认批大小与并发数
PREWARM_BATCH_SIZE = 64
PREWARM_CONCURRENCY = 8

SHAPE_SPEECH_ONLY = "speech_only"
SHAPE_SPEECH_PLUS_ARCHIVE = "speech_plus_archive"

#: 每题一个独立库：只装当前题自带的 haystack（在 oracle 档位下无跨题干扰，在 _s 档位下含本题约 40 场干扰）
ISOLATION_PER_ITEM = "per_item"
#: 全部题先灌入同一个库建完索引、再统一查询：衡量跨场记忆库滚到上万条规模后的真实召回与稀释
ISOLATION_SHARED = "shared"


class _Item:
    """一道题在库里的落地情况：会话数、轮数、答案轮主键与档案行回指表。"""

    def __init__(self, raw: Mapping[str, Any]) -> None:
        self.question_id = str(raw["question_id"])
        self.question = str(raw["question"])
        self.answer = str(raw["answer"])
        self.question_type = str(raw["question_type"])
        self.session_count = len(raw["haystack_sessions"])
        self.turn_count = sum(len(sess) for sess in raw["haystack_sessions"])
        self.answer_chat_ids: Set[int] = set()
        self.archive_to_chat: Dict[int, int] = {}
        self.last_turn_ms = BASE_START_MS

    @property
    def answerable(self) -> bool:
        return bool(self.answer_chat_ids)


async def _build_item(db: SQLiteDatabase, raw: Mapping[str, Any], start_ms: int, shape: str) -> _Item:
    """把一道题的 haystack 灌进库：每个会话一场正式场，每轮一条她播出的台词（可选再配一条同源档案行）。"""
    item = _Item(raw)
    cursor_ms = start_ms
    for sess_index, sess in enumerate(raw["haystack_sessions"]):
        session_started = cursor_ms
        session_id = await db.sessions.insert_live_session(
            stream_id=f"lme-{item.question_id}-{sess_index}",
            platform="lme",
            started_at_ms=session_started,
            title=f"LME {item.question_id} #{sess_index}",
            source="manual",
            kind="live",
        )
        last_in_session = session_started
        for turn_index, turn in enumerate(sess):
            content = str(turn.get("content") or "")
            if not content.strip():
                # 官方数据偶有空白轮（如 longmemeval_s 含 12 条空串），跳过以免触发空文本嵌入异常
                continue
            timestamp_ms = session_started + turn_index * TURN_GAP_MS
            last_in_session = timestamp_ms
            chat_id = await db.chat.insert_live_chat(
                live_session_id=session_id,
                timestamp_ms=timestamp_ms,
                sender_role="assistant",
                content=content,
                message_type="text",
                sender_name="streamer",
            )
            if shape == SHAPE_SPEECH_PLUS_ARCHIVE:
                title = content.strip().replace("\n", " ")[:ARCHIVE_TITLE_CHARS]
                line_id = await db.archive.insert_line(
                    live_session_id=session_id,
                    text=title,
                    quote=content.strip(),
                    evidence_chat_id=chat_id,
                )
                if line_id:
                    item.archive_to_chat[int(line_id)] = int(chat_id)
            if turn.get("has_answer"):
                item.answer_chat_ids.add(int(chat_id))
        await db.sessions.close_live_session(live_session_id=session_id, ended_at_ms=last_in_session + TURN_GAP_MS)
        item.last_turn_ms = last_in_session
        cursor_ms = last_in_session + SESSION_GAP_MS
    return item


async def _drain_index(db: SQLiteDatabase, embedding: Optional[EmbeddingClient], clock_ms: int) -> None:
    """循环跑生产索引任务，直到全部待补行的全文（以及可用时的向量）都补完。

    生产 ``MemoryIndexer.run_once`` 单轮全文上限 4000 行、向量上限 10 批，大库一轮补不完；
    评测必须先把索引补齐再开始查，否则后灌进去的题目根本不在索引里。
    """
    indexer = MemoryIndexer(
        repo=db.memory_index,
        embedding=embedding,
        embedding_note="嵌入已装配" if embedding else "语义一路没开",
        clock=lambda: clock_ms,
    )
    model_key = embedding.model_key if embedding is not None else None
    previous: Optional[int] = None
    while True:
        await indexer.run_once()
        remaining = await db.memory_index.pending_total(model_key=model_key, now_ms=clock_ms)
        if remaining == 0 or remaining == previous:
            break
        previous = remaining


def _entry_covered_chat_ids(entry: Any, archive_to_chat: Mapping[int, int]) -> Set[int]:
    """还原单条回想结果实际承载的台词主键集合（兼容升级前无 covered_chat_ids 与升级后带邻句拼合两种形态）。"""
    covered: Set[int] = {int(cid) for cid in (getattr(entry, "covered_chat_ids", None) or ()) if cid is not None}
    if entry.source == "speech":
        covered.add(int(entry.source_id))
    elif entry.source == "archive":
        mapped = archive_to_chat.get(int(entry.source_id))
        if mapped is not None:
            covered.add(int(mapped))
    return covered


def _judge(
    item: _Item,
    outcome: RecallOutcome,
    archive_to_chat: Mapping[int, int],
) -> Dict[str, Any]:
    """根据 ``MemoryRecallService.recall_detailed`` 真实入选的条目算正向命中与副作用指标。"""
    answer_ids = item.answer_chat_ids
    seen_chat_ids: Set[int] = set()
    slot_sets: List[Set[int]] = []
    duplicate_entries = 0
    irrelevant_entries = 0
    for entry in outcome.entries[:MAX_RESULTS]:
        slot_ids = _entry_covered_chat_ids(entry, archive_to_chat)
        if not slot_ids:
            continue
        # 若当前坑位承载的全部台词都已被前面更靠前的坑位承载，记为重复占坑
        if slot_ids.issubset(seen_chat_ids):
            duplicate_entries += 1
        seen_chat_ids.update(slot_ids)
        slot_sets.append(slot_ids)
        if not (slot_ids & answer_ids):
            irrelevant_entries += 1
    answer_lower = item.answer.strip().lower()
    return {
        "answerable": bool(answer_ids),
        "returned": len(outcome.entries),
        "unique_turns": len(seen_chat_ids),
        "duplicate_entries": duplicate_entries,
        "irrelevant_entries": irrelevant_entries,
        "top1": bool(slot_sets) and bool(slot_sets[0] & answer_ids),
        "in_top8": bool(seen_chat_ids & answer_ids),
        "contains_answer": bool(answer_lower) and answer_lower in outcome.text.lower(),
    }


def _resolve_model_config(explicit: Optional[Path]) -> Optional[Path]:
    """定位 model.toml：优先用显式传入路径，其次当前仓库根，独立工作树下再回退到主仓库的 config/model.toml。"""
    if explicit is not None:
        return explicit if explicit.exists() else None
    direct = REPO_ROOT / "config" / "model.toml"
    if direct.exists():
        return direct
    git_marker = REPO_ROOT / ".git"
    if git_marker.is_file():
        # 工作树里的 .git 文件形如 gitdir: D:/.../Amaidesu/.git/worktrees/<name>，往回找主仓库根
        first_line = git_marker.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
        if first_line.lower().startswith("gitdir:"):
            gitdir = Path(first_line.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (REPO_ROOT / gitdir).resolve()
            for parent in gitdir.parents:
                if parent.name == ".git":
                    candidate = parent.parent / "config" / "model.toml"
                    if candidate.exists():
                        return candidate
                    break
    return None


class _VectorDiskCache:
    """按 (model_key, 截断后文本 SHA256) 把真实嵌入服务算出的 float32 向量落盘复用，避免多题共享干扰场重复请求。"""

    def __init__(self, inner: EmbeddingClient, cache_path: Path) -> None:
        self._inner = inner
        self._cache_path = cache_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(cache_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vector_cache ("
            "model TEXT NOT NULL, text_sha256 TEXT NOT NULL, vec BLOB NOT NULL, "
            "PRIMARY KEY (model, text_sha256))"
        )
        self._conn.commit()
        self._lock = asyncio.Lock()

    @property
    def model_key(self) -> str:
        return self._inner.model_key

    @property
    def failures(self) -> int:
        return self._inner.failures

    @property
    def backing_off(self) -> bool:
        return self._inner.backing_off

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _normalize(text: str) -> str:
        # 送往远端嵌入前截到安全长度，防超长单轮触发 400 毒丸；空串兜底单空格避免中断整轮
        trimmed = text.strip()[:MAX_EMBED_INPUT_CHARS]
        return trimmed if trimmed else " "

    @classmethod
    def _digest(cls, text: str) -> str:
        return hashlib.sha256(cls._normalize(text).encode("utf-8")).hexdigest()

    def _lookup_blobs(self, digests: Sequence[str]) -> Dict[str, bytes]:
        """按指纹批量查本地缓存；分片避开 SQLite 变量上限。"""
        found: Dict[str, bytes] = {}
        unique = list(dict.fromkeys(digests))
        for start in range(0, len(unique), 500):
            chunk = unique[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT text_sha256, vec FROM vector_cache WHERE model = ? AND text_sha256 IN ({placeholders})",  # noqa: S608
                (self.model_key, *chunk),
            ).fetchall()
            for sha, blob in rows:
                found[str(sha)] = bytes(blob)
        return found

    def _store_vectors(self, pairs: Sequence[tuple[str, Sequence[float]]]) -> None:
        if not pairs:
            return
        rows = [(self.model_key, sha, np.asarray(vec, dtype="<f4").tobytes()) for sha, vec in pairs]
        self._conn.executemany(
            "INSERT OR REPLACE INTO vector_cache (model, text_sha256, vec) VALUES (?, ?, ?)",
            rows,
        )
        self._conn.commit()

    async def _embed_with_retry(self, safe_batch: List[str]) -> List[List[float]]:
        """调真实嵌入客户端并在瞬时网络抖动时重试至多 4 次，避免长程评测中途断流。"""
        last_exc: Optional[EmbeddingError] = None
        for attempt in range(4):
            try:
                self._inner._failures = 0
                self._inner._retry_at_ms = 0
                return await self._inner.embed(safe_batch)
            except EmbeddingError as exc:
                last_exc = exc
                wait_s = 2**attempt
                logger.warning(f"嵌入批次瞬时失败（第 {attempt + 1}/4 次，{wait_s}s 后重试）：{exc}")
                await asyncio.sleep(wait_s)
        assert last_exc is not None
        raise last_exc

    async def prewarm(self, texts: Sequence[str], concurrency: int = PREWARM_CONCURRENCY) -> int:
        """把一批文本里尚未缓存的项按批并发算完写入 SQLite，返回本次新算的条数。"""
        by_digest: Dict[str, str] = {}
        for raw in texts:
            safe = self._normalize(raw)
            by_digest.setdefault(self._digest(safe), safe)
        if not by_digest:
            return 0
        existing = self._lookup_blobs(list(by_digest.keys()))
        missing = [(sha, txt) for sha, txt in by_digest.items() if sha not in existing]
        if not missing:
            logger.info(f"向量缓存全命中（共 {len(by_digest)} 条唯一文本），无需请求远端")
            return 0
        logger.info(
            f"向量预热：唯一文本 {len(by_digest)} 条，缓存已命中 {len(existing)} 条，"
            f"待请求远端 {len(missing)} 条（并发 {concurrency}，批大小 {PREWARM_BATCH_SIZE}）"
        )
        sem = asyncio.Semaphore(max(1, concurrency))
        batches = [missing[i : i + PREWARM_BATCH_SIZE] for i in range(0, len(missing), PREWARM_BATCH_SIZE)]
        completed = 0
        t0 = time.perf_counter()

        async def _worker(batch: List[tuple[str, str]]) -> None:
            nonlocal completed
            async with sem:
                vecs = await self._embed_with_retry([txt for _, txt in batch])
            async with self._lock:
                self._store_vectors([(sha, vec) for (sha, _), vec in zip(batch, vecs, strict=True)])
                completed += len(batch)
                if completed % 2560 < len(batch) or completed == len(missing):
                    elapsed = max(0.1, time.perf_counter() - t0)
                    rate = completed / elapsed
                    eta_s = int((len(missing) - completed) / max(rate, 0.1))
                    logger.info(f"向量预热进度：{completed}/{len(missing)}（{rate:.1f} 条/秒，预计剩余 {eta_s} 秒）")

        await asyncio.gather(*(_worker(b) for b in batches))
        return len(missing)

    async def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """实现与 EmbeddingClient 相同的异步接口：优先走本地缓存，未命中的走远端算完回填。"""
        safe_texts = [self._normalize(t) for t in texts]
        digests = [self._digest(t) for t in safe_texts]
        async with self._lock:
            blobs = self._lookup_blobs(digests)
        missing_map: Dict[str, str] = {}
        for sha, safe in zip(digests, safe_texts, strict=True):
            if sha not in blobs and sha not in missing_map:
                missing_map[sha] = safe
        if missing_map:
            missing_items = list(missing_map.items())
            for start in range(0, len(missing_items), PREWARM_BATCH_SIZE):
                chunk = missing_items[start : start + PREWARM_BATCH_SIZE]
                vecs = await self._embed_with_retry([txt for _, txt in chunk])
                async with self._lock:
                    self._store_vectors([(sha, vec) for (sha, _), vec in zip(chunk, vecs, strict=True)])
            async with self._lock:
                blobs.update(self._lookup_blobs(list(missing_map.keys())))
        return [np.frombuffer(blobs[sha], dtype="<f4").tolist() for sha in digests]


async def _load_embedding(
    model_config_path: Optional[Path] = None,
    disable_embedding: bool = False,
) -> Optional[EmbeddingClient]:
    """按 config/model.toml 的 [embedding] 段只读装配嵌入客户端；没配、显式禁用或装配失败返回 None。

    自己用 tomllib 只读解析，不走 ``load_config_dir``（那条路会把漂移写回磁盘）；独立工作树下自动回退主仓配置。
    """
    if disable_embedding:
        logger.info("已指定 --no-embedding，语义一路按没开处理")
        return None
    config_path = _resolve_model_config(model_config_path)
    if config_path is None:
        logger.info("未找到 config/model.toml，语义一路按没开处理")
        return None
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
        emb_raw = dict(raw.get("embedding", {}) or {})
        # 评测预热按至少 64 一批请求，兼顾网关批上限与吞吐
        if int(emb_raw.get("batch_size") or 0) < PREWARM_BATCH_SIZE:
            emb_raw["batch_size"] = PREWARM_BATCH_SIZE
        embedding_cfg = EmbeddingConfig.model_validate(emb_raw)
        providers = [LLMProviderConfig.model_validate(item) for item in (raw.get("llm_providers", []) or [])]
    except Exception as exc:  # noqa: BLE001 - 离线评测读不到本地配置时降级只测关键词路，记日志带原因
        logger.warning(f"读 model.toml 失败，语义一路按没开处理：{type(exc).__name__}: {exc}", exc=True)
        return None

    setup = build_embedding_client(embedding_cfg, providers)
    logger.info(f"配置来源 {config_path}；装配结果：{setup.note}")
    return setup.client


def _record_judged(
    item: _Item,
    outcome: RecallOutcome,
    judged: Mapping[str, Any],
    overall: Counter[str],
    per_type_total: Counter[str],
    per_type_hit: Counter[str],
    per_type_contains: Counter[str],
    per_type_top1: Counter[str],
    details: List[Dict[str, Any]],
    examples: List[Dict[str, Any]],
    keep_examples: int,
) -> None:
    """把单题判定累加到总表与分题型计数器里（有答案题算召回，无答案题单算误塞副作用）。"""
    overall["returned_total"] += int(judged["returned"])
    overall["duplicate_entries"] += int(judged["duplicate_entries"])
    if judged["answerable"]:
        overall["answerable"] += 1
        overall["top1"] += int(judged["top1"])
        overall["in_top8"] += int(judged["in_top8"])
        overall["contains_answer"] += int(judged["contains_answer"])
        overall["answerable_irrelevant_entries"] += int(judged["irrelevant_entries"])
        per_type_total[item.question_type] += 1
        per_type_hit[item.question_type] += int(judged["in_top8"])
        per_type_contains[item.question_type] += int(judged["contains_answer"])
        per_type_top1[item.question_type] += int(judged["top1"])
    else:
        overall["unanswerable"] += 1
        overall["unanswerable_nonempty"] += int(int(judged["returned"]) > 0)
        overall["unanswerable_returned"] += int(judged["returned"])

    details.append(
        {
            "question_id": item.question_id,
            "question_type": item.question_type,
            "judged": dict(judged),
        }
    )
    if len(examples) < keep_examples:
        examples.append(
            {
                "question_id": item.question_id,
                "question": item.question,
                "answer": item.answer,
                "judged": dict(judged),
                "result": outcome.text,
            }
        )


def _make_recall_service(
    db: SQLiteDatabase,
    embedding: Any,
    clock_ms: int,
) -> MemoryRecallService:
    search = MemorySearch(repo=db.memory_index, embedding=embedding)
    return MemoryRecallService(
        chat_repo=db.chat,
        commitments_repo=db.commitments,
        archive_repo=db.archive,
        current_session_provider=lambda: None,
        clock=lambda: clock_ms,
        event_repo=None,
        saves_provider=dict,
        search=search,
    )


async def _init_fast_temp_db(path: Path) -> SQLiteDatabase:
    """初始化用完即弃的临时评测库（沿用生产 WAL 连接配置）。"""
    db = SQLiteDatabase(path)
    await db.initialize()
    return db


async def _prewarm_db_vectors(
    db: SQLiteDatabase,
    cache: _VectorDiskCache,
    questions: Sequence[str],
    clock_ms: int,
    concurrency: int,
) -> None:
    """从临时库的 pending_vectors 取出生产索引器将要嵌入的精确正文连同全部问题文本，一次性并发预热到本地缓存。"""
    texts: List[str] = [q for q in questions if q and q.strip()]
    for source in ("speech", "archive"):
        docs = await db.memory_index.pending_vectors(
            source=source,
            model_key=cache.model_key,
            now_ms=clock_ms,
            limit=1_000_000,
        )
        texts.extend(doc.document for doc in docs)
    await cache.prewarm(texts, concurrency=concurrency)


async def run(
    dataset_path: Path,
    limit: int,
    out_path: Optional[Path],
    shape: str,
    keep_examples: int,
    isolation: str,
    model_config_path: Optional[Path] = None,
    disable_embedding: bool = False,
    vector_cache_path: Optional[Path] = None,
    embed_concurrency: int = PREWARM_CONCURRENCY,
) -> int:
    raw_items: List[Dict[str, Any]] = json.loads(dataset_path.read_text(encoding="utf-8"))
    if limit > 0:
        raw_items = raw_items[:limit]
    raw_embedding = await _load_embedding(model_config_path=model_config_path, disable_embedding=disable_embedding)
    cache_wrapper: Optional[_VectorDiskCache] = None
    embedding: Any = raw_embedding
    if raw_embedding is not None:
        resolved_cache = vector_cache_path or (REPO_ROOT / "docs" / "tmp" / "longmemeval" / "vector_cache.sqlite")
        cache_wrapper = _VectorDiskCache(raw_embedding, resolved_cache)
        embedding = cache_wrapper
    logger.info(
        f"待测题数 {len(raw_items)}；建库形态 {shape}；隔离方式 {isolation}；"
        f"嵌入服务 {f'已装配（{raw_embedding.model_key}）' if raw_embedding else '未装配（只走关键词一路）'}"
    )

    overall: Counter[str] = Counter()
    per_type_total: Counter[str] = Counter()
    per_type_hit: Counter[str] = Counter()
    per_type_contains: Counter[str] = Counter()
    per_type_top1: Counter[str] = Counter()
    details: List[Dict[str, Any]] = []
    examples: List[Dict[str, Any]] = []
    session_counts: List[int] = []
    turn_counts: List[int] = []
    started = time.time()

    try:
        if isolation == ISOLATION_PER_ITEM:
            if cache_wrapper is not None and raw_items:
                # 先在一份汇总临时库里用生产索引仓储算出全部题目的精确待嵌正文并并发预热，避免 500 个独立库重复串行打远端
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as warm_tmp:
                    warm_db = await _init_fast_temp_db(Path(warm_tmp) / "warm.db")
                    try:
                        cursor_ms = BASE_START_MS
                        for raw in raw_items:
                            built = await _build_item(warm_db, raw, start_ms=cursor_ms, shape=shape)
                            cursor_ms = built.last_turn_ms + SESSION_GAP_MS
                        await _prewarm_db_vectors(
                            warm_db,
                            cache_wrapper,
                            [str(r.get("question") or "") for r in raw_items],
                            clock_ms=cursor_ms + INDEX_SETTLE_LAG_MS,
                            concurrency=embed_concurrency,
                        )
                    finally:
                        await warm_db.close()
            for index, raw in enumerate(raw_items):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    db = await _init_fast_temp_db(Path(tmp) / "lme.db")
                    try:
                        item = await _build_item(db, raw, start_ms=BASE_START_MS, shape=shape)
                        session_counts.append(item.session_count)
                        turn_counts.append(item.turn_count)
                        clock_ms = item.last_turn_ms + INDEX_SETTLE_LAG_MS
                        await _drain_index(db, embedding, clock_ms=clock_ms)
                        service = _make_recall_service(db, embedding, clock_ms=clock_ms)
                        outcome = await service.recall_detailed(item.question, scope=SCOPE_ALL)
                        judged = _judge(item, outcome, item.archive_to_chat)
                        _record_judged(
                            item,
                            outcome,
                            judged,
                            overall,
                            per_type_total,
                            per_type_hit,
                            per_type_contains,
                            per_type_top1,
                            details,
                            examples,
                            keep_examples,
                        )
                    finally:
                        await db.close()
                if (index + 1) % 50 == 0:
                    logger.info(f"已跑 {index + 1}/{len(raw_items)} 题")
        else:
            # 全部题共用一库：必须先把全部语料灌完、索引一次补齐，再统一跑查询，各题面对同样大小的完整库
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                db = await _init_fast_temp_db(Path(tmp) / "lme.db")
                try:
                    built_items: List[_Item] = []
                    global_archive_to_chat: Dict[int, int] = {}
                    cursor_ms = BASE_START_MS
                    for raw in raw_items:
                        item = await _build_item(db, raw, start_ms=cursor_ms, shape=shape)
                        built_items.append(item)
                        session_counts.append(item.session_count)
                        turn_counts.append(item.turn_count)
                        global_archive_to_chat.update(item.archive_to_chat)
                        cursor_ms = item.last_turn_ms + SESSION_GAP_MS
                    clock_ms = cursor_ms + INDEX_SETTLE_LAG_MS
                    if cache_wrapper is not None:
                        await _prewarm_db_vectors(
                            db,
                            cache_wrapper,
                            [it.question for it in built_items],
                            clock_ms=clock_ms,
                            concurrency=embed_concurrency,
                        )
                    await _drain_index(db, embedding, clock_ms=clock_ms)
                    service = _make_recall_service(db, embedding, clock_ms=clock_ms)
                    for index, item in enumerate(built_items):
                        outcome = await service.recall_detailed(item.question, scope=SCOPE_ALL)
                        judged = _judge(item, outcome, global_archive_to_chat)
                        _record_judged(
                            item,
                            outcome,
                            judged,
                            overall,
                            per_type_total,
                            per_type_hit,
                            per_type_contains,
                            per_type_top1,
                            details,
                            examples,
                            keep_examples,
                        )
                        if (index + 1) % 50 == 0:
                            logger.info(f"已跑 {index + 1}/{len(built_items)} 题")
                finally:
                    await db.close()
    finally:
        if cache_wrapper is not None:
            cache_wrapper.close()

    total = len(raw_items)
    answerable = overall["answerable"]
    unanswerable = overall["unanswerable"]
    returned_total = overall["returned_total"]
    report = {
        "数据集": {
            "路径": dataset_path.name,
            "题数": total,
            "有答案轮的题数": answerable,
            "无答案轮的题数": unanswerable,
            "每题场次数_中位": round(float(statistics.median(session_counts)), 1) if session_counts else 0.0,
            "每题轮数_中位": round(float(statistics.median(turn_counts)), 1) if turn_counts else 0.0,
            "总轮数": sum(turn_counts),
            "建库形态": shape,
            "隔离方式": isolation,
        },
        "检索配置": {
            "语义一路": f"已装配（{raw_embedding.model_key}）" if raw_embedding else "未装配（只走关键词一路）",
            "召回路数": "生产的 MemorySearch（关键词 BM25 + 可选语义，两路 RRF 融合）",
            "工具": "生产的 MemoryRecallService.recall_detailed（含同源台词与档案合并）",
            "命中判定": "按回想工具真实入选条目的主键判（台词主键；档案行按证据台词主键换算）",
        },
        "正向召回（仅计有答案轮的题）": {
            "A_前8条含答案轮": round(overall["in_top8"] / answerable, 4) if answerable else None,
            "A_命中数": overall["in_top8"],
            "B_第1条是答案轮": round(overall["top1"] / answerable, 4) if answerable else None,
            "B_命中数": overall["top1"],
            "C_结果文本含答案字符串": round(overall["contains_answer"] / answerable, 4) if answerable else None,
            "C_命中数": overall["contains_answer"],
        },
        "副作用指标": {
            "返回总条数": returned_total,
            "同源重复条数": overall["duplicate_entries"],
            "同源重复率": round(overall["duplicate_entries"] / returned_total, 4) if returned_total else 0.0,
            "有答案题平均伴随无关条数": (
                round(overall["answerable_irrelevant_entries"] / answerable, 2) if answerable else 0.0
            ),
            "无答案题非空率": round(overall["unanswerable_nonempty"] / unanswerable, 4) if unanswerable else None,
            "无答案题平均返回条数": round(overall["unanswerable_returned"] / unanswerable, 2) if unanswerable else None,
        },
        "按题型": {
            qtype: {
                "题数": per_type_total[qtype],
                "A_前8条含答案轮": (
                    round(per_type_hit[qtype] / per_type_total[qtype], 4) if per_type_total[qtype] else None
                ),
                "B_第1条是答案轮": (
                    round(per_type_top1[qtype] / per_type_total[qtype], 4) if per_type_total[qtype] else None
                ),
                "C_结果文本含答案字符串": (
                    round(per_type_contains[qtype] / per_type_total[qtype], 4) if per_type_total[qtype] else None
                ),
            }
            for qtype in sorted(per_type_total)
        },
        "耗时秒": round(time.time() - started, 1),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"报告已写入 {out_path}")
        stem = out_path.stem
        details_path = out_path.parent / f"{stem}-details.json"
        details_path.write_text(json.dumps(details, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"逐题明细已写入 {details_path}")
        if examples:
            examples_path = out_path.parent / f"{stem}-examples.json"
            examples_path.write_text(json.dumps(examples, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"样例已写入 {examples_path}")
    return 0


def _selftest() -> int:
    """自检：核对灌库、完整补算、同源台词与档案合并、先灌后测与副作用指标均按预期工作。"""
    answerable_raw = {
        "question_id": "selftest-1",
        "question_type": "selftest",
        "question": "What kind of camera did I buy last spring?",
        "answer": "A Fujifilm X-T5 mirrorless camera",
        "haystack_sessions": [
            [
                {"role": "user", "content": "I am thinking about buying a camera this spring.", "has_answer": False},
                {
                    "role": "assistant",
                    "content": "I bought a Fujifilm X-T5 mirrorless camera last spring for travel photos.",
                    "has_answer": True,
                },
                {
                    "role": "user",
                    "content": "How is the battery life on that camera model?",
                    "has_answer": False,
                },
            ]
        ],
    }
    unanswerable_raw = {
        "question_id": "selftest-abs",
        "question_type": "selftest",
        "question": "What camera lens did I rent in winter?",
        "answer": "Unknown",
        "haystack_sessions": [
            [
                {"role": "user", "content": "We cooked tomato soup yesterday.", "has_answer": False},
            ]
        ],
    }

    async def _run() -> Dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            db = SQLiteDatabase(Path(tmp) / "selftest.db")
            await db.initialize()
            first = await _build_item(db, answerable_raw, start_ms=BASE_START_MS, shape=SHAPE_SPEECH_PLUS_ARCHIVE)
            second = await _build_item(
                db,
                unanswerable_raw,
                start_ms=first.last_turn_ms + SESSION_GAP_MS,
                shape=SHAPE_SPEECH_PLUS_ARCHIVE,
            )
            clock_ms = second.last_turn_ms + INDEX_SETTLE_LAG_MS
            await _drain_index(db, embedding=None, clock_ms=clock_ms)
            archive_map = {**first.archive_to_chat, **second.archive_to_chat}
            service = _make_recall_service(db, embedding=None, clock_ms=clock_ms)
            hit_outcome = await service.recall_detailed(first.question, scope=SCOPE_ALL)
            hit_judged = _judge(first, hit_outcome, archive_map)
            abs_outcome = await service.recall_detailed(second.question, scope=SCOPE_ALL)
            abs_judged = _judge(second, abs_outcome, archive_map)
            await db.close()
            return {"hit": hit_judged, "abs": abs_judged}

    result = asyncio.run(_run())
    hit = result["hit"]
    abstention = result["abs"]
    ok = (
        hit["answerable"]
        and hit["in_top8"]
        and hit["contains_answer"]
        and hit["returned"] == 3
        and hit["duplicate_entries"] == 0
        and not abstention["answerable"]
    )
    print("selftest", "PASS" if ok else f"FAIL {json.dumps(result, ensure_ascii=False)}")
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LongMemEval 跨场召回与副作用评测（临时库，走生产检索链路）")
    parser.add_argument(
        "--dataset",
        "--oracle",
        dest="dataset",
        type=Path,
        default=None,
        help="LongMemEval 数据集 JSON（如 longmemeval_s.json 或 longmemeval_oracle.json）",
    )
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（0 = 全跑）")
    parser.add_argument("--out", type=Path, default=None, help="报告写到哪（JSON）")
    parser.add_argument(
        "--shape",
        choices=[SHAPE_SPEECH_ONLY, SHAPE_SPEECH_PLUS_ARCHIVE],
        default=SHAPE_SPEECH_ONLY,
        help="建库形态：每轮只建台词，还是台词加同源档案行",
    )
    parser.add_argument("--examples", type=int, default=3, help="留几道题的完整渲染结果做样例")
    parser.add_argument(
        "--isolation",
        choices=[ISOLATION_PER_ITEM, ISOLATION_SHARED],
        default=ISOLATION_PER_ITEM,
        help="库隔离方式：per_item 每题独立库，shared 先灌完全部语料再统一查询",
    )
    parser.add_argument(
        "--model-config", type=Path, default=None, help="指定 model.toml 路径（默认自动查找工作树或主仓配置）"
    )
    parser.add_argument("--no-embedding", action="store_true", help="强制关闭语义向量路，仅跑关键词基线")
    parser.add_argument("--vector-cache", type=Path, default=None, help="本地向量缓存 SQLite 路径")
    parser.add_argument(
        "--embed-concurrency",
        type=int,
        default=PREWARM_CONCURRENCY,
        help=f"向量预热并发请求数（默认 {PREWARM_CONCURRENCY}）",
    )
    parser.add_argument("--selftest", action="store_true", help="自检：核对灌库、同源合并与副作用指标")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.dataset is None:
        parser.error("要跑评测得给 --dataset")
    return asyncio.run(
        run(
            args.dataset,
            args.limit,
            args.out,
            args.shape,
            args.examples,
            args.isolation,
            model_config_path=args.model_config,
            disable_embedding=args.no_embedding,
            vector_cache_path=args.vector_cache,
            embed_concurrency=args.embed_concurrency,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
