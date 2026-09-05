"""UtteranceQueue - 主播发言播放队列（FIFO 串行调度器）

本模块是 StreamerAgent 的发言下游编排器：
- 持有 FIFO 播放队列（容量受 `max_queue` 限制）
- 后台 worker 串行调用 speak 可调用对象（``await self._speak(text, utterance_id)``）
- 与 TTS 引擎解耦：TTS 引擎自行发布 ``tts.utterance.*`` 生命周期事件，
  本队列不参与事件发布，避免越权。

设计原则（与 StreamerAgent 发言管线约束一致）：
- 队列溢出采用"丢最旧"策略（FIFO 满时先弹队首再入队）
- 单 worker 串行保证播放顺序，避免叠加/打断
- 入队即返回（fire-and-forget），不阻塞上游决策循环的 tick 节奏
- 单 utterance 超时由 `render_timeout_ms` 看门狗保护（防止合成/播放卡死拖垮队列）
- 丢弃的 utterance 不进入 TTS 引擎，自然不会触发 ``tts.utterance.*`` 事件

约束（AGENTS.md）：
- 时刻字段使用 int 毫秒；时长字段命名 ``_ms``
- 不引用文档章节编号 / ADR 编号
- 内部函数就近组织，避免超长单文件
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Deque, Optional

from src.modules.logging import get_logger


# 默认队列容量
DEFAULT_MAX_QUEUE = 3

# 默认单 utterance 等待超时（毫秒）：覆盖"合成 + 播放"全周期。
# 播放时长与语音等长（长句可达数十秒），此值是防引擎卡死的兜底，
# 不是正常耗时的上限；超时取消时引擎侧会停止已提交的播放。
DEFAULT_RENDER_TIMEOUT_MS = 60_000

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
    """

    utterance_id: str
    text: str


class UtteranceQueue:
    """发言播放队列（FIFO + 单 worker 串行调度）。

    行为契约：
    - 队列满时入队会先丢弃队首（最旧的 pending），记录 WARN 日志。
      丢弃的 utterance 不会进入 TTS 引擎，因此也不会触发
      ``tts.utterance.*`` 事件——丢消息由本队列全权负责。
    - 后台 worker 串行处理：一条处理完再取下一条。
    - 单条 utterance 处理使用 `asyncio.wait_for(..., timeout=render_timeout_ms)`
      包裹，触发 TimeoutError 时取消当前任务、记录 ERROR 并继续下一条。
    - ``start()`` 幂等：重复调用不会重建任务；``stop()`` 后再次 ``start()``
      会重建 worker。

    注意：本类只持有 speak 可调用对象与 ``EventBus``；``tts.utterance.*``
    事件由 TTS 引擎自身在合成/播放完成时发布。
    """

    def __init__(
        self,
        *,
        speak: SpeakCallable,
        logger_name: str = "UtteranceQueue",
        max_queue: int = DEFAULT_MAX_QUEUE,
        render_timeout_ms: int = DEFAULT_RENDER_TIMEOUT_MS,
    ) -> None:
        """构造发言队列。

        Args:
            speak: speak 可调用对象；签名 ``async (text, utterance_id) -> None``。
                编排队列对外引擎无关：传入真实 TTS 引擎 ``handle_speech``
                适配器或测试 mock 均可。
            logger_name: 日志记录器名（默认 ``UtteranceQueue``）。
            max_queue: 队列容量上限（必须 ≥ 1）。
            render_timeout_ms: 单 utterance 调用超时（毫秒；必须 ≥ 100）。

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
        self._logger = get_logger(logger_name)
        self._max_queue = max_queue
        self._render_timeout_ms = render_timeout_ms

        self._queue: Deque[_UtteranceItem] = deque()
        # 有可用项目时唤醒 worker（队列入队 + worker 等待）
        self._wakeup = asyncio.Event()
        # 控制 worker 循环；置位后 worker 在下一次等待时优雅退出
        self._stopping = asyncio.Event()
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()

        # 统计（轻量自监控）
        self._total_enqueued = 0
        self._total_dropped = 0
        self._total_processed = 0
        self._total_timeouts = 0
        self._total_errors = 0

    # ---------------- 公共 API ----------------

    async def enqueue(self, utterance_id: str, text: str) -> bool:
        """将一条 utterance 投入播放队列。

        当队列已满，先弹出队首（最旧的 pending），记录 WARN 日志，
        再将本条入队。被丢弃的 utterance 不会触发 ``tts.utterance.*``
        事件。

        Args:
            utterance_id: 编排层生成的唯一 ID（关联 reply/TTS/存储）。
            text: 待合成文本。

        Returns:
            是否成功入队（本方法永不返回 False——drop 后再入队，恒入队）。
            返回 True 仅用于表达"被接受"的语义；队列停用时返回 False 表示拒绝。
        """
        if self._stopping.is_set():
            self._logger.warning(f"队列正在停止，拒绝入队: utterance_id={utterance_id}")
            return False

        item = _UtteranceItem(utterance_id=utterance_id, text=text)
        async with self._lock:
            dropped_id: Optional[str] = None
            if len(self._queue) >= self._max_queue:
                # 丢最旧（队首）
                oldest = self._queue.popleft()
                dropped_id = oldest.utterance_id
                self._total_dropped += 1
                self._logger.warning(
                    f"队列已满（容量 {self._max_queue}），丢弃最旧 utterance: utterance_id={dropped_id}"
                )
            self._queue.append(item)
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
                self._logger.warning("worker 停止时抛出异常（已忽略）: {}", exc)

        self._logger.info(
            f"UtteranceQueue 已停止（enqueued={self._total_enqueued}, "
            f"processed={self._total_processed}, dropped={self._total_dropped}）"
        )

    @property
    def size(self) -> int:
        """当前队列深度（仅用于诊断/测试）。"""
        return len(self._queue)

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
                    if not self._queue:
                        self._wakeup.clear()
                        continue
                    item = self._queue.popleft()
                    if not self._queue:
                        # 取完后队列为空，清除 wakeup 让下一次 enqueue 再唤醒
                        self._wakeup.clear()

                # 在锁外执行 speak（避免阻塞其他 enqueue）
                await self._dispatch(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - worker 边界兜底
            self._logger.opt(exception=True).error("worker 循环异常退出: {}", exc)

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
        except asyncio.CancelledError:
            # worker 被取消时向上抛，让 worker_loop 退出
            raise
        except Exception as exc:  # noqa: BLE001 - 防御性兜底
            self._total_errors += 1
            self._logger.error(
                f"speak 调用异常（已忽略，继续下一条）: utterance_id={item.utterance_id}, err={exc}",
                exc_info=True,
            )


__all__ = [
    "UtteranceQueue",
    "SpeakCallable",
    "DEFAULT_MAX_QUEUE",
    "DEFAULT_RENDER_TIMEOUT_MS",
]
