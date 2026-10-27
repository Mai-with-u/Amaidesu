"""UtteranceQueue - 主播发言播放队列（按优先级排队的串行调度器）

本模块是 StreamerAgent 的发言下游编排器：
- 持有播放队列（容量受 `max_queue` 限制）：优先级高的排前面，同级先来先播；
  不传优先级时全部同级，退化为原来的 FIFO
- 后台 worker 串行调用 speak 可调用对象（``await self._speak(text, utterance_id)``）
- 与 TTS 引擎解耦：引擎正常路径的 ``tts.utterance.started`` / ``finished``
  由引擎自行发布；队列只在"这句到不了引擎"的两条路径（渲染超时、队列溢出
  丢弃）代发 ``tts.utterance.failed``，让字幕等消费者逐句降级出字，不哑不悬。

设计原则（StreamerAgent 发言管线约束）：
- 队列溢出丢"优先级最低里最旧的"一条；新来的比队里所有条目都低时丢新来的
  （全部同级时就是丢最旧，与原来一致）
- 反应类台词带过期时刻：轮到它时已经过时就跳过，过时的"哇什么东西"比不说还怪
- 长段话的几句标同一个段落号：正在播的段落被更高优先级的台词插进来时，剩下没播的句子
  直接丢掉，不在突发反应之后接着讲
- 单 worker 串行保证播放顺序，避免叠加/打断
- 入队即返回（fire-and-forget），不阻塞上游决策循环的 tick 节奏
- 单 utterance 超时由 `render_timeout_ms` 看门狗保护（防止合成/播放卡死拖垮队列）
- 渲染超时与队列溢出丢弃时，队列代发 ``tts.utterance.failed``——这句到不了
  引擎，引擎不会替它发事件，字幕兜底出字的唯一机会在队列这里
- ``wait_idle`` 暴露"嘴空"判定（队列空且当前句播完），供发言管线做
  背压等待（发言节奏跟随语音播放）——是否等待由调用方决定，本队列只
  提供状态与阻塞原语

约定：
- 时刻字段使用 int 毫秒；时长字段命名 ``_ms``
- 内部函数就近组织，避免超长单文件
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Deque, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import UtteranceFailedPayload
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms


# 默认队列容量
DEFAULT_MAX_QUEUE = 3

# 默认单 utterance 等待超时（毫秒）：覆盖"合成 + 播放"全周期。
# 播放时长与语音等长（长句可达数十秒），此值是防引擎卡死的兜底，
# 不是正常耗时的上限；超时取消时引擎侧会停止已提交的播放。
DEFAULT_RENDER_TIMEOUT_MS = 60_000

# 默认发言等播放的等待上限（毫秒）：覆盖"当前句剩余播放 + 队列积压数句"
# 的最坏排空时间，量级对齐 render_timeout_ms；0 表示不限时（不推荐，
# 引擎故障时决策循环会被无限拖住——引擎侧 fail 快速排空则不受影响）。
DEFAULT_WAIT_PLAYBACK_TIMEOUT_MS = 60_000

# 队列代发失败事件时 payload 的 engine 标识：这两条路径发生在队列层，
# 引擎未参与，填队列自身标识与引擎名（edge/gptsovits 等）区分来源。
QUEUE_ENGINE_ID = "utterance_queue"

# speak 可调用对象的形态：
#   await speak(text: str, utterance_id: Optional[str]) -> None
# 编排队列对外引擎无关：传入真实 TTS 引擎 ``handle_speech`` 适配器或
# 测试 mock 均可；构造期校验鸭子类型（callable + 可 await）。
SpeakCallable = Callable[[str, Optional[str]], Awaitable[None]]


@dataclass(slots=True)
class _UtteranceItem:
    """队列内单条 utterance 的不可变载荷。

    Attributes:
        utterance_id: 编排层生成的唯一 ID（格式 ``utt_{epoch_ms}_{seq}``）。
        text: 待合成的台词文本。
        priority: 优先级，越大越先播；同级先来先播。
        expires_at_ms: 过期时刻（Unix 毫秒），轮到它时已过就跳过；0 = 不过期。
        segment: 段落号：长段话拆成的几句共用一个，空串 = 独立一句。
        on_start: 轮到这句、交给 TTS 的那一刻调用一次；没轮到就被丢掉时不调用。
    """

    utterance_id: str
    text: str
    priority: int = 0
    expires_at_ms: int = 0
    segment: str = ""
    on_start: Optional[Callable[[], None]] = None


class UtteranceQueue:
    """发言播放队列（FIFO + 单 worker 串行调度）。

    行为契约：
    - 队列满时入队会先丢弃队首（最旧的 pending），记录 WARN 日志；
      被丢弃的 utterance 不会进入 TTS 引擎，由本队列代发
      ``tts.utterance.failed``（``error_message`` 标注 ``queue_overflow``），
      字幕等消费者据此兜底出字。
    - 后台 worker 串行处理：一条处理完再取下一条。
    - 单条 utterance 处理使用 `asyncio.wait_for(..., timeout=render_timeout_ms)`
      包裹，触发 TimeoutError 时取消当前任务、代发
      ``tts.utterance.failed``（``error_message`` 标注 ``render_timeout``）、
      记录 ERROR 并继续下一条。
    - ``start()`` 幂等：重复调用不会重建任务；``stop()`` 后再次 ``start()``
      会重建 worker。

    注意：本类持有 speak 可调用对象与可选 ``EventBus``。引擎正常路径的
    ``tts.utterance.started`` / ``finished`` 仍由引擎发布；队列只在上述
    两条"到不了引擎"的路径代发 failed 事件（未注入事件总线时只记日志）。
    """

    def __init__(
        self,
        *,
        speak: SpeakCallable,
        event_bus: Optional[EventBus] = None,
        logger_name: str = "UtteranceQueue",
        max_queue: int = DEFAULT_MAX_QUEUE,
        render_timeout_ms: int = DEFAULT_RENDER_TIMEOUT_MS,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        """构造发言队列。

        Args:
            speak: speak 可调用对象；签名 ``async (text, utterance_id) -> None``。
                编排队列对外引擎无关：传入真实 TTS 引擎 ``handle_speech``
                适配器或测试 mock 均可。
            event_bus: 事件总线（可选）；渲染超时与溢出丢弃时代发
                ``tts.utterance.failed``，缺省 None 时这两条路径只记日志。
            logger_name: 日志记录器名（默认 ``UtteranceQueue``）。
            max_queue: 队列容量上限（必须 ≥ 1）。
            render_timeout_ms: 单 utterance 调用超时（毫秒；必须 ≥ 100）。
            clock: 当前时刻（Unix 毫秒）；判定过期用，测试可注入。

        Raises:
            ValueError: 入参越界时。
            TypeError: ``speak`` 不可调用时。
        """
        if not callable(speak):
            raise TypeError(f"UtteranceQueue: speak 必须是 callable，得到 {type(speak).__name__}")
        if max_queue < 1:
            raise ValueError(f"max_queue 必须 ≥ 1，实际 {max_queue}")
        if render_timeout_ms < 100:
            raise ValueError(f"render_timeout_ms 必须 ≥ 100，实际 {render_timeout_ms}")

        self._speak = speak
        self._event_bus = event_bus
        self._logger = get_logger(logger_name)
        self._max_queue = max_queue
        self._render_timeout_ms = render_timeout_ms
        self._clock = clock
        # 最近开播的那一句所属的段落：更高优先级的台词插进来时，这个段落剩下的句子作废
        self._current_segment = ""

        self._queue: Deque[_UtteranceItem] = deque()
        # 有可用项目时唤醒 worker（队列入队 + worker 等待）
        self._wakeup = asyncio.Event()
        # 控制 worker 循环；置位后 worker 在下一次等待时优雅退出
        self._stopping = asyncio.Event()
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()
        # worker 正在处理一条 utterance（合成+播放全周期）的标志：
        # 与队列深度共同构成 busy 判定（wait_idle 的谓词）；取件临界区内
        # 置位、_dispatch 结束 finally 复位并唤醒 idle 等待方
        self._processing = False
        # "变闲"通知：wait_idle 等待方在此挂起，worker 完成一条后唤醒
        self._idle_cond = asyncio.Condition()

        # 统计（轻量自监控）
        self._total_enqueued = 0
        self._total_dropped = 0
        self._total_processed = 0
        self._total_timeouts = 0
        self._total_errors = 0
        self._total_expired = 0
        self._total_interrupted = 0

    # ---------------- 公共 API ----------------

    async def enqueue(
        self,
        utterance_id: str,
        text: str,
        *,
        priority: int = 0,
        ttl_ms: int = 0,
        segment: str = "",
        on_start: Optional[Callable[[], None]] = None,
    ) -> bool:
        """将一条 utterance 投入播放队列。

        按优先级插队（同级排在已有同级之后）。队列已满时丢掉优先级最低里最旧的一条，
        记录 WARN 日志；新来的比队里所有条目都低时丢新来的这条。被丢弃的 utterance
        不会触发引擎的 ``tts.utterance.started`` / ``finished`` 事件，由本队列
        代发 ``tts.utterance.failed``。

        Args:
            utterance_id: 编排层生成的唯一 ID（关联 reply/TTS/存储）。
            text: 待合成文本。
            priority: 优先级，越大越先播（默认 0：全部同级即原来的 FIFO）。
            ttl_ms: 多久之内没轮到就作废（0 = 不过期）。突发反应用：过时了不该再说。
            segment: 段落号：长段话拆成的几句共用一个。
            on_start: 轮到这句、交给 TTS 的那一刻调用一次（流式引擎此刻发
                ``tts.utterance.started``，字幕和口型也从这时开始）；用来记它实际
                排了多久，回调出错只记日志不挡播放。

        Returns:
            是否被接受；队列停用、或队满时新条目优先级最低被丢掉时返回 False。
        """
        if self._stopping.is_set():
            self._logger.warning(f"队列正在停止，拒绝入队: utterance_id={utterance_id}")
            return False

        expires_at_ms = self._clock() + ttl_ms if ttl_ms > 0 else 0
        item = _UtteranceItem(
            utterance_id=utterance_id,
            text=text,
            priority=priority,
            expires_at_ms=expires_at_ms,
            segment=segment,
            on_start=on_start,
        )
        async with self._lock:
            self._drop_interrupted_segment(item)
            if len(self._queue) >= self._max_queue:
                # 丢优先级最低里最旧的；全部同级时就是丢最旧（队首），与原来一致
                candidates = [queued for queued in self._queue if queued.priority <= item.priority]
                if not candidates:
                    self._total_dropped += 1
                    self._logger.warning(
                        f"队列已满（容量 {self._max_queue}）且都比这条急，丢弃新来的 utterance: utterance_id={utterance_id}"
                    )
                    await self._emit_failed(utterance_id, text, "queue_overflow: 队列已满且新条目优先级最低")
                    return False
                victim = min(candidates, key=lambda queued: queued.priority)
                self._queue.remove(victim)
                self._total_dropped += 1
                self._logger.warning(
                    f"队列已满（容量 {self._max_queue}），丢弃最旧 utterance: utterance_id={victim.utterance_id}"
                )
                await self._emit_failed(victim.utterance_id, victim.text, "queue_overflow: 队列已满，丢弃最旧条目")
            self._insert_by_priority(item)
            self._total_enqueued += 1

        # 唤醒可能正在等待的 worker（即便 stopping 状态也 set，保证退出检查）
        self._wakeup.set()
        return True

    async def start(self) -> None:
        """启动后台 worker 任务（幂等：重复调用不会重建任务）。

        若 ``start()`` 调用前已有 utterance 入队，worker 启动后会立即被
        唤醒并消费——因此 ``start()`` 内不清空 wakeup 信号（清空反而会
        导致 pre-start 入队的条目被永久挂起）。
        """
        async with self._lock:
            if self._worker_task is not None and not self._worker_task.done():
                return
            # 仅清除停止位；wakeup 信号保留以承接 pre-start 入队。
            self._stopping.clear()
            self._worker_task = asyncio.create_task(self._worker_loop(), name=f"{self.__class__.__name__}.worker")
        self._logger.info(
            f"UtteranceQueue 已启动（max_queue={self._max_queue}, render_timeout_ms={self._render_timeout_ms}）"
        )

    async def stop(self) -> None:
        """停止后台 worker：置位停止事件并取消任务，丢弃未播队列。

        已入队但尚未被 worker 取走的 utterance 会被直接丢弃（不会被
        TTS 引擎消费，自然不会产生 ``tts.utterance.*`` 事件）。stop()
        不会等待正在进行的 invoke 完成——若 invoke 已进入 TTS 引擎，
        由 TTS 引擎自身的超时/取消机制兜底。
        """
        async with self._lock:
            task = self._worker_task
            self._worker_task = None
            self._stopping.set()
            self._wakeup.set()  # 唤醒可能阻塞在 wait 的 worker
            if self._queue:
                # 残留未播放的 utterances 直接丢弃（不入 TTS）
                dropped = len(self._queue)
                self._total_dropped += dropped
                self._queue.clear()
                self._logger.warning(f"停止时丢弃未播放的 utterances: count={dropped}")

        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._logger.warning(f"worker 停止时抛出异常（已忽略）: {exc}")

        self._logger.info(
            f"UtteranceQueue 已停止（enqueued={self._total_enqueued}, "
            f"processed={self._total_processed}, dropped={self._total_dropped}）"
        )

    def _insert_by_priority(self, item: _UtteranceItem) -> None:
        """插到第一个比它优先级低的条目前面；同级排在已有同级之后（同级先来先播）。"""
        for index, queued in enumerate(self._queue):
            if queued.priority < item.priority:
                self._queue.insert(index, item)
                return
        self._queue.append(item)

    def _drop_interrupted_segment(self, item: _UtteranceItem) -> None:
        """正在播的段落被更高优先级的台词插进来：段落里还没播的句子作废，不在反应之后接着讲。

        段落还没开播时不算打断——急的先播，段落随后整段照常播。
        """
        if not self._current_segment or item.segment == self._current_segment:
            return
        rest = [
            queued
            for queued in self._queue
            if queued.segment == self._current_segment and queued.priority < item.priority
        ]
        for queued in rest:
            self._queue.remove(queued)
        if rest:
            self._total_interrupted += len(rest)
            self._logger.info(
                f"段落被插断，丢弃剩余 {len(rest)} 句: segment={self._current_segment}, 插队={item.utterance_id}"
            )

    @property
    def size(self) -> int:
        """当前队列深度（仅用于诊断/测试）。"""
        return len(self._queue)

    @property
    def busy(self) -> bool:
        """是否仍有发声占用（队列有待播条目或 worker 正在合成/播放）。"""
        return bool(self._queue) or self._processing

    async def wait_idle(self, timeout_ms: Optional[int] = None) -> bool:
        """阻塞等待嘴空（队列排空且当前句播完），返回是否达到空闲。

        供发言管线做背压：上一句没播完就不放行下一句，使发言节奏跟随
        语音播放。``timeout_ms=None`` 表示不限时；超时返回 ``False`` 不抛
        异常（放行与否由调用方决定）。worker 完成一条 utterance 时唤醒
        等待方重新判定谓词；空闲状态调用立即返回 ``True``。
        """

        def _is_idle() -> bool:
            return not self._queue and not self._processing

        if _is_idle():
            return True
        timeout_s: Optional[float] = None if timeout_ms is None else timeout_ms / 1000.0
        try:
            async with self._idle_cond:
                await asyncio.wait_for(self._idle_cond.wait_for(_is_idle), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def is_running(self) -> bool:
        """worker 是否仍在运行（用于测试断言）。"""
        return self._worker_task is not None and not self._worker_task.done() and not self._stopping.is_set()

    def get_stats(self) -> dict[str, int]:
        """获取队列运行统计。"""
        return {
            "enqueued": self._total_enqueued,
            "processed": self._total_processed,
            "dropped": self._total_dropped,
            "timeouts": self._total_timeouts,
            "errors": self._total_errors,
            "expired": self._total_expired,
            "interrupted": self._total_interrupted,
            "queue_size": len(self._queue),
            "max_queue": self._max_queue,
        }

    # ---------------- 内部：worker 循环 ----------------

    async def _worker_loop(self) -> None:
        """后台 worker：循环取一条 → speak → 下一条，串行执行。"""
        try:
            while not self._stopping.is_set():
                # 等待队列有内容（被 wakeup 唤醒或退出时被 stopping 唤醒）
                await self._wakeup.wait()
                if self._stopping.is_set():
                    return

                # 在锁内取一条；取出后清空 wakeup，避免空转
                async with self._lock:
                    if self._stopping.is_set():
                        return
                    item = self._pop_fresh()
                    if item is None:
                        self._wakeup.clear()
                        continue
                    self._current_segment = item.segment
                    if not self._queue:
                        # 取完后队列为空，清除 wakeup 让下一次 enqueue 再唤醒
                        self._wakeup.clear()
                    # 置位与取件同临界区：wait_idle 的谓词读 (queue, processing)
                    # 二元组，同步段内事件循环不切换协程，不会观察到
                    # "队列已空但 processing 未置位"的假空闲窗口
                    self._processing = True

                # 在锁外执行 speak（避免阻塞其他 enqueue）；交给 TTS 之前先告诉上游"轮到这句了"
                self._notify_start(item)
                try:
                    await self._dispatch(item)
                finally:
                    self._processing = False
                    async with self._idle_cond:
                        self._idle_cond.notify_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - worker 边界兜底
            self._logger.exception(f"worker 循环异常退出: {exc}")

    def _notify_start(self, item: _UtteranceItem) -> None:
        """轮到这句时回调一次；回调是旁路记录，出错只记日志，这句照常播。"""
        if item.on_start is None:
            return
        try:
            item.on_start()
        except Exception as exc:  # noqa: BLE001 - 旁路回调失败不挡播放
            self._logger.warning(f"开播回调失败（已忽略）: utterance_id={item.utterance_id}, err={exc}", exc=True)

    async def _emit_failed(self, utterance_id: str, text: str, error_message: str) -> None:
        """这句到不了引擎，代发 ``tts.utterance.failed`` 让字幕兜底出字。

        ``emit`` 为后台扇出（事件总线自行 create_task 分发），不阻塞调用方。
        未注入事件总线、或发布失败时只记日志，不影响队列继续运转。
        """
        if self._event_bus is None:
            return
        payload = UtteranceFailedPayload(
            utterance_id=utterance_id,
            speech_text=text,
            engine=QUEUE_ENGINE_ID,
            error_message=error_message,
        )
        try:
            await self._event_bus.emit(
                CoreEvents.TTS_UTTERANCE_FAILED,
                payload,
                source="streamer_agent.utterance_queue",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底事件不阻断队列
            self._logger.warning(f"代发 tts.utterance.failed 失败（已忽略）: utterance_id={utterance_id}, err={exc}")

    def _pop_fresh(self) -> Optional[_UtteranceItem]:
        """取下一条还没过期的；过期的直接作废（反应过时了再说只会更怪）。"""
        now = self._clock()
        while self._queue:
            item = self._queue.popleft()
            if item.expires_at_ms and item.expires_at_ms <= now:
                self._total_expired += 1
                self._logger.info(f"utterance 已过期，跳过: utterance_id={item.utterance_id}")
                continue
            return item
        return None

    async def _dispatch(self, item: _UtteranceItem) -> None:
        """单条 utterance 的处理入口（speak + 超时保护 + 错误兜底）。

        speak 可调用对象可能抛出异常（引擎 fail / 网络断等），本方法在外层
        捕获 asyncio.TimeoutError / 任何意外异常，保证 worker 永远能继续下一条。
        """
        timeout_s = self._render_timeout_ms / 1000.0
        try:
            await asyncio.wait_for(
                self._speak(item.text, item.utterance_id),
                timeout=timeout_s,
            )
            self._total_processed += 1
        except asyncio.TimeoutError:
            self._total_timeouts += 1
            self._logger.error(
                f"utterance 等待超时（>{self._render_timeout_ms}ms），放弃等待并继续下一条"
                f"（引擎取消清理后不会发声）: utterance_id={item.utterance_id}"
            )
            await self._emit_failed(item.utterance_id, item.text, f"render_timeout: 超过 {self._render_timeout_ms}ms")
        except asyncio.CancelledError:
            # worker 被取消时向上抛，让 worker_loop 退出
            raise
        except Exception as exc:  # noqa: BLE001 - 防御性兜底
            # speak 引擎内部已打过带上下文的错误日志，此处只计数并继续，
            # 不再重复打 traceback（连接类环境故障会刷数百行噪声）
            self._total_errors += 1
            self._logger.error(f"speak 调用异常（已忽略，继续下一条）: utterance_id={item.utterance_id}, err={exc}")


__all__ = [
    "UtteranceQueue",
    "SpeakCallable",
    "QUEUE_ENGINE_ID",
    "DEFAULT_MAX_QUEUE",
    "DEFAULT_RENDER_TIMEOUT_MS",
    "DEFAULT_WAIT_PLAYBACK_TIMEOUT_MS",
]
