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
from typing import Callable, Dict, Optional, Tuple

from src.modules.background import BackgroundTaskRegistry
from src.modules.llm.embedding import EmbeddingClient, EmbeddingError
from src.modules.logging import get_logger
from src.modules.storage.repos.memory_index import INDEX_SOURCES, IndexSource, MemoryIndexRepo
from src.modules.utils.time_utils import now_ms

from .textmatch import index_text

# 日志里四类来源的叫法
_SOURCE_LABELS: Dict[IndexSource, str] = {
    "archive": "档案",
    "commitment": "承诺",
    "speech": "台词",
    "event": "游戏事件",
    "social_fact": "游戏社交事实",
}

logger = get_logger("MemoryIndexer")

# 每隔多久跑一轮（毫秒）
INDEX_INTERVAL_MS = 5_000
# 全文每次从仓储取多少行；每类来源每轮至多取这么多次（启动补算时一轮补不完，下一轮接着补）
FULL_TEXT_BATCH = 200
FULL_TEXT_MAX_BATCHES = 20
# 向量每类来源每轮至多请求几批（每批条数由嵌入配置的 batch_size 定）
VECTOR_MAX_BATCHES = 10

# 缺行数超过这个门槛才登记进后台任务面板：平时每轮三五行的零星补漏不值得
# 上面板（会不停闪进闪出），启动补算/换嵌入模型时的成百上千行缺才值得看见
BACKFILL_ALERT_THRESHOLD = 100

# 索引补算在后台任务登记处的任务号（进程内只有这一个补算任务）
BACKFILL_TASK_ID = "memory-index-backfill"

# 补算任务在后台任务面板上的标题
BACKFILL_TASK_TITLE = "记忆索引补算"


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
        task_registry: Optional[BackgroundTaskRegistry] = None,
        alert_threshold: int = BACKFILL_ALERT_THRESHOLD,
    ) -> None:
        """仓储与嵌入客户端由主播 Agent 构造时注入。

        task_registry 是后台任务登记处：缺行总数超过 alert_threshold 时登记
        "记忆索引补算"任务（进度=还缺 N 行），归零即注销；门槛以下的零星补漏
        不登记，免得面板闪进闪出。缺省 None 时不登记，补算行为不变。
        """
        self._repo = repo
        self._embedding = embedding
        self._embedding_note = embedding_note
        self._clock = clock
        self._interval_s = max(interval_ms, 100) / 1000
        self._vector_batch = max(1, vector_batch)
        self._task_registry = task_registry
        self._alert_threshold = max(1, int(alert_threshold))
        # 补算任务当前是否登记在面板上（门槛以下只报进度不新建，归零即注销）
        self._backfill_registered = False
        self._task_registry = task_registry
        self._alert_threshold = max(1, int(alert_threshold))
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
        if self._embedding is not None and self._embedding.failures:
            return f"语义检索暂不可用：嵌入服务连续失败 {self._embedding.failures} 次，退避中"
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
        if self._backfill_registered:
            # 停机时补算不可能继续了：按失败注销，面板不留一个永远"进行中"的行
            self._backfill_registered = False
            if self._task_registry is not None:
                await self._task_registry.finish(BACKFILL_TASK_ID, success=False, failure_reason="记忆索引任务已停机")

    def request_run(self) -> None:
        """叫醒后台立刻补一轮（下播整理写完时调用）。"""
        self._wake.set()

    async def _loop(self) -> None:
        """补一轮 → 等到下一个间隔或被叫醒 → 再补一轮；单轮出错记日志后照常进下一轮。"""
        while self._running:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 后台循环边界：一轮失败不能让索引永远停摆
                logger.exception(f"记忆索引这一轮失败，下一轮重试：{exc}")
                if self._task_registry is not None and self._backfill_registered:
                    # 登记中的补算轮失败：按失败注销，下一轮缺行还在门槛上会重新登记
                    self._backfill_registered = False
                    await self._task_registry.finish(
                        BACKFILL_TASK_ID, success=False, failure_reason=f"这一轮补算失败：{exc}"
                    )
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def run_once(self) -> None:
        """跑一轮：四类来源先补全文，再（配置了且不在退避期时）补向量；这一轮补了多少记一行日志。"""
        await self._reconcile_backfill_task()
        now = self._clock()
        full_text: Dict[IndexSource, int] = {}
        for source in INDEX_SOURCES:
            full_text[source] = await self._fill_full_text(source, now)
        vectors: Dict[IndexSource, int] = {}
        # 退避由嵌入客户端统一管（查询向量失败也算）：退避期间这一轮只补全文
        if self._embedding is not None and not self._embedding.backing_off:
            for source in INDEX_SOURCES:
                written, ok = await self._fill_vectors(self._embedding, source, now)
                vectors[source] = written
                if not ok:
                    break
        self._passes += 1
        self._log_pass(full_text, vectors)
        await self._reconcile_backfill_task()

    async def _reconcile_backfill_task(self) -> None:
        """按当前缺行总数对齐后台任务面板：超门槛登记并报"还缺 N 行"，归零注销。

        门槛以下的零星补漏不登记（面板不为三五行的缺闪进闪出）；已登记但缺行
        落到门槛以下而未归零时保持登记继续报进度，直到归零（补算总要补完）。
        登记处未装配时什么都不做，补算行为不变。
        """
        registry = self._task_registry
        if registry is None:
            return
        model_key = self._embedding.model_key if self._embedding is not None else None
        missing = await self._repo.pending_total(model_key=model_key, now_ms=self._clock())
        if missing == 0:
            if self._backfill_registered:
                self._backfill_registered = False
                await registry.finish(BACKFILL_TASK_ID, success=True)
            return
        if missing < self._alert_threshold and not self._backfill_registered:
            return
        progress_text = f"还缺 {missing} 行"
        if self._backfill_registered:
            await registry.update(BACKFILL_TASK_ID, progress_text)
        else:
            self._backfill_registered = True
            await registry.begin(BACKFILL_TASK_ID, BACKFILL_TASK_TITLE, progress_text=progress_text)

    def _log_pass(self, full_text: Dict[IndexSource, int], vectors: Dict[IndexSource, int]) -> None:
        """补了东西记 info（每类来源各几行）；什么都没补只记 debug，免得空转的轮次刷屏。"""
        parts = [
            f"{kind}：{'、'.join(f'{_SOURCE_LABELS[source]} {count}' for source, count in counts.items() if count)}"
            for kind, counts in (("全文", full_text), ("向量", vectors))
            if any(counts.values())
        ]
        if parts:
            logger.info(f"记忆索引这一轮补了 {'；'.join(parts)}")
        else:
            logger.debug("记忆索引这一轮没有要补的")

    async def _fill_full_text(self, source: IndexSource, now: int) -> int:
        """一类来源的全文：取缺的行、两字切分、写回，返回写了几行；台词切词时拼入同场前 30 秒紧邻台词而不改单句正本指纹。"""
        total = 0
        for _ in range(FULL_TEXT_MAX_BATCHES):
            docs = await self._repo.pending_full_text(source=source, now_ms=now, limit=FULL_TEXT_BATCH)
            if not docs:
                return total
            windows: Dict[int, Tuple[str, Optional[int], str]] = {}
            if source == "speech":
                window_fn = getattr(self._repo, "speech_window_rows", None)
                if callable(window_fn):
                    windows = await window_fn(chat_ids=[doc.source_id for doc in docs])
            items = []
            for doc in docs:
                prev_content = windows.get(doc.source_id, ("", None, ""))[2] if windows else ""
                fts_doc = f"{prev_content}\n{doc.document}" if prev_content else doc.document
                items.append((doc, index_text(fts_doc)))
            written = await self._repo.write_full_text(source=source, items=items)
            total += written
            if len(docs) < FULL_TEXT_BATCH or written == 0:
                return total
        return total

    async def _fill_vectors(self, embedding: EmbeddingClient, source: IndexSource, now: int) -> Tuple[int, bool]:
        """一类来源的向量，返回（写了几行, 嵌入是否正常）；嵌入失败时客户端已登记退避，这一轮其余来源的向量也不再请求。"""
        total = 0
        for _ in range(VECTOR_MAX_BATCHES):
            docs = await self._repo.pending_vectors(
                source=source, model_key=embedding.model_key, now_ms=now, limit=self._vector_batch
            )
            if not docs:
                return total, True
            try:
                vectors = await embedding.embed([doc.document for doc in docs])
            except EmbeddingError as exc:
                logger.debug(f"这一轮的向量补算停在 {_SOURCE_LABELS[source]}：{exc}")
                return total, False
            written = await self._repo.write_vectors(
                source=source, model_key=embedding.model_key, items=list(zip(docs, vectors, strict=True))
            )
            total += written
            if len(docs) < self._vector_batch or written == 0:
                return total, True
        return total, True


__all__ = ["MemoryIndexer"]
