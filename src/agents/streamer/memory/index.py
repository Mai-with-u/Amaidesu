"""记忆检索的索引任务：后台把档案行、承诺、她的台词、游戏事件补进全文与向量索引。

主播 Agent 内部的后台件，代码直连，不是工具、不注册。按"缺什么补什么"工作，不记进度水位：
每轮问仓储哪些该收的行还缺全文行、缺当前模型的向量，算好写回。收什么、不收什么由仓储一处定
（正式场、播出满 30 秒、游戏事件白名单……），这里不另判。

- **触发**：每隔几秒跑一轮；下播整理写完时立即补一轮（本场新写的档案行与承诺马上能被查到）。
- **启动补算**：第一轮就是补算——升级后的旧库、换了嵌入模型（向量的模型标识对不上）的行都会被
  当作"缺"补上；在后台慢慢补，不挡 Agent 启动。
- **先全文、后向量**：全文是本地切词，毫秒级；向量要调嵌入服务。嵌入失败时这一轮的向量放弃，
  按连续失败次数翻倍退避（封顶），记 warning 带原因、次数与多久后重试；退避期间全文照常补，
  检索照样有关键词一路可用。没配嵌入服务时只补全文。
"""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from src.modules.llm.embedding import EmbeddingClient, EmbeddingError
from src.modules.logging import get_logger
from src.modules.storage.repos.memory_index import INDEX_SOURCES, IndexSource, MemoryIndexRepo
from src.modules.utils.time_utils import now_ms

from .textmatch import index_text

logger = get_logger("MemoryIndexer")

# 每隔多久跑一轮（毫秒）
INDEX_INTERVAL_MS = 5_000
# 全文每次从仓储取多少行；每类来源每轮至多取这么多次（启动补算时一轮补不完，下一轮接着补）
FULL_TEXT_BATCH = 200
FULL_TEXT_MAX_BATCHES = 20
# 向量每类来源每轮至多请求几批（每批条数由嵌入配置的 batch_size 定）
VECTOR_MAX_BATCHES = 10
# 嵌入失败后的退避：从 30 秒起翻倍，封顶 10 分钟
BACKOFF_BASE_MS = 30_000
BACKOFF_MAX_MS = 600_000


class MemoryIndexer:
    """记忆索引的后台补算任务（全文必开，向量配置了嵌入服务才开）。"""

    def __init__(
        self,
        *,
        repo: MemoryIndexRepo,
        embedding: Optional[EmbeddingClient],
        embedding_note: str,
        clock: Callable[[], int] = now_ms,
        interval_ms: int = INDEX_INTERVAL_MS,
        vector_batch: int = 32,
    ) -> None:
        self._repo = repo
        self._embedding = embedding
        self._embedding_note = embedding_note
        self._clock = clock
        self._interval_s = max(interval_ms, 100) / 1000
        self._vector_batch = max(1, vector_batch)
        self._failures = 0
        self._retry_at_ms = 0
        self._passes = 0
        self._running = False
        self._task: Optional["asyncio.Task[None]"] = None
        self._wake = asyncio.Event()

    @property
    def passes(self) -> int:
        """已跑完的轮数（给测试与状态面板看后台有没有在转）。"""
        return self._passes

    @property
    def semantic_note(self) -> str:
        """语义检索的状态说明：开着用什么模型、没开为什么、或正在退避。"""
        if self._embedding is not None and self._failures:
            return f"语义检索暂不可用：嵌入服务连续失败 {self._failures} 次，退避中"
        return self._embedding_note

    async def start(self) -> None:
        """启动后台循环；第一轮就是启动补算，在后台跑，不挡调用方。"""
        if self._running:
            return
        self._running = True
        logger.info(f"记忆索引任务启动：{self._embedding_note}")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """停掉后台循环（正在跑的一轮被取消，没写完的行下次启动再补）。"""
        self._running = False
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def request_run(self) -> None:
        """叫醒后台立刻补一轮（下播整理写完时调用）。"""
        self._wake.set()

    async def _loop(self) -> None:
        """补一轮 → 等到下一个间隔或被叫醒 → 再补一轮；单轮出错记日志后照常进下一轮。"""
        while self._running:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 后台循环边界：一轮失败不能让索引永久停摆
                logger.exception(f"记忆索引这一轮失败，下一轮重试：{exc}")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def run_once(self) -> None:
        """跑一轮：四类来源先补全文，再（配置了且不在退避期时）补向量。"""
        now = self._clock()
        for source in INDEX_SOURCES:
            await self._fill_full_text(source, now)
        if self._embedding is not None and now >= self._retry_at_ms:
            for source in INDEX_SOURCES:
                if not await self._fill_vectors(self._embedding, source, now):
                    break
        self._passes += 1

    async def _fill_full_text(self, source: IndexSource, now: int) -> None:
        """一类来源的全文：取缺的行、两字切分、写回；整批都被跳过（读写之间正文变了）就留到下一轮。"""
        for _ in range(FULL_TEXT_MAX_BATCHES):
            docs = await self._repo.pending_full_text(source=source, now_ms=now, limit=FULL_TEXT_BATCH)
            if not docs:
                return
            written = await self._repo.write_full_text(
                source=source, items=[(doc, index_text(doc.document)) for doc in docs]
            )
            if len(docs) < FULL_TEXT_BATCH or written == 0:
                return

    async def _fill_vectors(self, embedding: EmbeddingClient, source: IndexSource, now: int) -> bool:
        """一类来源的向量；嵌入失败时登记退避并返回 False（这一轮其余来源的向量也不再请求）。"""
        for _ in range(VECTOR_MAX_BATCHES):
            docs = await self._repo.pending_vectors(
                source=source, model_key=embedding.model_key, now_ms=now, limit=self._vector_batch
            )
            if not docs:
                return True
            try:
                vectors = await embedding.embed([doc.document for doc in docs])
            except EmbeddingError as exc:
                self._arm_backoff(now, exc)
                return False
            self._failures = 0
            written = await self._repo.write_vectors(
                source=source, model_key=embedding.model_key, items=list(zip(docs, vectors, strict=True))
            )
            if len(docs) < self._vector_batch or written == 0:
                return True
        return True

    def _arm_backoff(self, now: int, exc: EmbeddingError) -> None:
        """嵌入失败：连续失败次数加一，按次数翻倍定下次重试的时刻，记 warning。"""
        self._failures += 1
        backoff_ms = min(BACKOFF_BASE_MS * 2 ** (self._failures - 1), BACKOFF_MAX_MS)
        self._retry_at_ms = now + backoff_ms
        logger.warning(
            f"语义索引暂不可用（连续失败 {self._failures} 次，{backoff_ms // 1000} 秒后重试；关键词检索照常）：{exc}"
        )


__all__ = ["MemoryIndexer"]
