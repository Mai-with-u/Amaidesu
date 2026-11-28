"""UtteranceQueue - 主播发言播放队列（按优先级排队的串行调度器）

本模块是 StreamerAgent 的发言下游编排器：
- 持有播放队列（容量受 `max_queue` 限制）：优先级高的排前面，同级先来先播；
  不传优先级时全部同级，等价于普通 FIFO 队列
- 后台 worker 串行调用 speak 可调用对象（``await self._speak(text, utterance_id)``）
- 与 TTS 引擎解耦：引擎正常路径的 ``tts.utterance.started`` / ``finished``
  由引擎自行发布；队列只在"这句没能正常播出、且引擎没替它报失败"的三条路径
  （渲染超时、队列溢出丢弃、speak 抛出未报告的异常）代发 ``tts.utterance.failed``，
  让字幕等消费者逐句降级出字，不会无声卡住也不会悬空等待。

设计原则（StreamerAgent 发言管线约束）：
- 队列溢出丢"优先级最低里最旧的"一条；新来的比队里所有条目都低时丢新来的
  （全部同级时就是丢最旧的队首，即普通 FIFO 行为）
- 反应类台词带过期时刻：轮到它时已经过时就跳过，过时的"哇什么东西"比不说还怪
- 长段话的几句标同一个段落号：正在播的段落被更高优先级的台词插进来时，剩下没播的句子
  直接丢掉，不在突发反应之后接着讲
- 单 worker 串行保证播放顺序，避免叠加/打断
- 入队即返回（fire-and-forget），不阻塞上游决策循环的 tick 节奏
- 单 utterance 超时由 `render_timeout_ms` 看门狗保护（防止合成/播放卡死拖垮队列）
- 渲染超时、队列溢出丢弃与 speak 抛异常时，队列代发 ``tts.utterance.failed``——
  这句到不了引擎，或引擎在发事件之前就失败了（如首句懒启动 setup() 抛错），
  引擎不会替它发事件，字幕兜底出字的机会在队列这里
- 引擎在合成或播放中途失败时会先自己发 failed、再抛异常，异常上带"已报告"
  标记（见 ``src.modules.events.payloads.utterance.mark_failure_reported``）；队列见到标记
  只计数打日志、不再补发，避免同一句话出两条 failed。没有标记的未知异常照常
  补发——辅助通道出问题时，宁可多出一次字幕，也不能让这句话没有字幕
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
from src.modules.events.payloads.core import CoreErrorPayload
from src.modules.events.payloads.utterance import UtteranceDroppedPayload, UtteranceFailedPayload, is_failure_reported
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms


# 四档语音优先级：数值越大越先播；兼容外部调用方传入更大整数（如旧调用传 5），
# 所有优先级比较均基于 `<` / `<=` 而非等值匹配。
# - NORMAL (0)：常规实况解说 / 冷场暖场独白
# - HIGHLIGHT (1)：高光即时反应 / 高优实况解说 / 游戏公屏即时反应
# - AUDIENCE (2)：观众弹幕 / 礼物 / SC / 上舰回复
# - EMERGENCY (3)：极急硬打断（非自选意外阵亡 / 紧急急停）
SPEECH_PRIORITY_NORMAL = 0
SPEECH_PRIORITY_HIGHLIGHT = 1
SPEECH_PRIORITY_AUDIENCE = 2
SPEECH_PRIORITY_EMERGENCY = 3

# 默认队列容量（放宽至 6：容纳长弹幕分 2–3 段播报叠加欢迎/实况解说，防轻易触发 queue_overflow）
DEFAULT_MAX_QUEUE = 6

# 默认单 utterance 等待超时（毫秒）：覆盖"合成 + 播放"全周期。
# 播放时长与语音等长（长句可达数十秒），此值是防引擎卡死的兜底，
# 不是正常耗时的上限；超时取消时引擎侧会停止已提交的播放。
DEFAULT_RENDER_TIMEOUT_MS = 60_000

# 渲染超时熔断参数：连续 render_timeout 达到阈值即熔断——熔断期间不再启动
# 渲染等待，队列内外的 utterance 立即代发 failed（字幕照常出字），避免渲染
# 减速时段逐句各自耗满超时、观众长时间听不到语音。熔断后每隔半开间隔放行
# 一句探路：成功即恢复正常，失败保持熔断并刷新计时。
RENDER_FUSE_THRESHOLD = 3
RENDER_FUSE_HALF_OPEN_MS = 60_000

# 同类运行故障告警的最小重发间隔（毫秒）：5 分钟窗口内只发布第一条 core.error，
# 持续故障不把 WebUI 告警刷屏；窗口判定用注入时钟，测试可控
ERROR_ALERT_THROTTLE_MS = 5 * 60 * 1000

# 默认发言等播放的等待上限（毫秒）：覆盖"当前句剩余播放 + 队列积压数句"
# 的最坏排空时间，量级对齐 render_timeout_ms；0 表示不限时（不推荐，
# 引擎故障时决策循环会被无限拖住——引擎侧 fail 快速排空则不受影响）。
DEFAULT_WAIT_PLAYBACK_TIMEOUT_MS = 60_000

# 默认发言放行的未播积压上限（句）：放行条件从"队列排空"放宽为"未播的话
# ≤ max_pending"——播放期间允许下一句先排队，规划器不用等嘴念完才有资格出声。
# 0 = 旧行为（等队列排空）。
DEFAULT_MAX_PENDING = 1

# 队列代发失败事件时 payload 的 engine 标识：这几条路径（渲染超时、溢出丢弃、
# 引擎没报告的 speak 异常）上引擎都没替这句发过 failed，填队列自身标识，
# 与引擎名（edge/gptsovits 等）区分来源。
QUEUE_ENGINE_ID = "utterance_queue"

# speak 可调用对象的形态：
#   await speak(text: str, utterance_id: Optional[str],
#               emotion: Optional[str], emotion_intensity: Optional[float]) -> None
# 编排队列对外引擎无关：传入真实 TTS 引擎 ``handle_speech`` 适配器或
# 测试 mock 均可；构造期校验鸭子类型（callable + 可 await）。
SpeakCallable = Callable[[str, Optional[str], Optional[str], Optional[float]], Awaitable[None]]


@dataclass(slots=True)
class _UtteranceItem:
    """队列内单条 utterance 的不可变载荷。

    Attributes:
        utterance_id: 编排层生成的唯一 ID（格式 ``utt_{epoch_ms}_{seq}``）。
        text: 待合成的台词文本。
        priority: 优先级，越大越先播；同级先来先播。
        expires_at_ms: 过期时刻（Unix 毫秒），轮到它时已过就跳过；0 = 不过期。
        segment: 段落号：长段话拆成的几句共用一个，空串 = 独立一句。
        emotion: 这句话的情绪提示（``Emotion`` 枚举值字符串）；随句透传给
            TTS 引擎（支持动态情感的引擎据此调整合成表现）。
        emotion_intensity: 情绪强度 0.0~1.0；与 ``emotion`` 成对出现。
        on_start: 轮到这句、交给 TTS 的那一刻调用一次；没轮到就被丢掉时不调用。
        on_interrupted: 段落被更急的台词插断、这句和同段后面的句子一起作废时调用一次，
            参数是作废的那几句拼起来的原文（同段作废几句只调一次）。
    """

    utterance_id: str
    text: str
    priority: int = SPEECH_PRIORITY_NORMAL
    expires_at_ms: int = 0
    segment: str = ""
    emotion: Optional[str] = None
    emotion_intensity: Optional[float] = None
    on_start: Optional[Callable[[], None]] = None
    on_interrupted: Optional[Callable[[str], None]] = None


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
    - 连续渲染超时达到 ``RENDER_FUSE_THRESHOLD`` 次触发熔断：熔断期间不再启动
      渲染等待，轮到的 utterance 立即代发 ``tts.utterance.failed``（``error_message``
      标注 ``render_timeout_fuse``），避免减速时段逐句各自耗满超时、观众长时间
      无语音；距最近一次渲染失败超过 ``RENDER_FUSE_HALF_OPEN_MS`` 放行一句探路，
      成功即恢复正常，失败保持熔断并刷新计时。
    - speak 抛出其他异常时记录 ERROR（不重复打 traceback）并继续下一条：异常
      未带"引擎已报告失败"标记就代发 ``tts.utterance.failed``（``error_message``
      标注 ``speak_error``）；带了标记说明引擎已经发过，只计数不补发。
    - ``start()`` 幂等：重复调用不会重建任务；``stop()`` 后再次 ``start()``
      会重建 worker。

    注意：本类持有 speak 可调用对象与可选 ``EventBus``。引擎正常路径的
    ``tts.utterance.started`` / ``finished`` 仍由引擎发布；队列只在上述
    三条"没能正常播出"的路径代发 failed 事件（引擎已报告过的失败除外；
    未注入事件总线时只记日志，队列照常继续）。
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
        on_undelivered: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """构造发言队列。

        Args:
            speak: speak 可调用对象；签名 ``async (text, utterance_id) -> None``。
                编排队列对外引擎无关：传入真实 TTS 引擎 ``handle_speech``
                适配器或测试 mock 均可。
            event_bus: 事件总线（可选）；渲染超时、溢出丢弃与 speak 抛出未报告
                异常时代发 ``tts.utterance.failed``，缺省 None 时这三条路径只记日志。
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
        # 未实际完播（dropped / failed）代码直连回调：参数 (utterance_id, status)，供主播侧释放同轮去重锚点
        self._on_undelivered = on_undelivered
        # 最近开播的那一句所属的段落：更高优先级的台词插进来时，这个段落剩下的句子作废
        self._current_segment = ""
        # 当前正在被 worker 播放的条目：取件临界区内与 _processing 同步置位，
        # 供二级硬打断核对在播条目优先级；播完或硬打断结束在 finally 内清零
        self._current_item: Optional[_UtteranceItem] = None
        # 单次 speak 执行封装成的独立可取消子任务：二级硬打断只取消该子任务，
        # 不取消 worker 自身，确保 worker 立刻继续播放紧随其后的紧急发言
        self._current_speak_task: Optional[asyncio.Task[None]] = None
        # 被定向二级硬打断的在播条目引用：用于把定向硬打断引起的 CancelledError
        # 与队列关停 / 渲染超时严格区分，三路互不串扰
        self._hard_interrupted_item: Optional[_UtteranceItem] = None

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

        # 渲染超时熔断状态：连续超时计数、是否熔断、最近一次渲染失败时刻
        self._consecutive_render_timeouts = 0
        self._render_fused = False
        self._last_render_failure_ms = 0
        # 上一次熔断告警（core.error）的发布时刻（毫秒）；0 = 从未发布过
        self._last_fuse_alert_ms = 0

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
        priority: int = SPEECH_PRIORITY_NORMAL,
        ttl_ms: int = 0,
        segment: str = "",
        emotion: Optional[str] = None,
        emotion_intensity: Optional[float] = None,
        on_start: Optional[Callable[[], None]] = None,
        on_interrupted: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """将一条 utterance 投入播放队列。

        按优先级插队（同级排在已有同级之后）。队列已满时丢掉优先级最低里最旧的一条，
        记录 WARN 日志；新来的比队里所有条目都低时丢新来的这条。被丢弃的 utterance
        不会触发引擎的 ``tts.utterance.started`` / ``finished`` 事件，由本队列
        代发 ``tts.utterance.failed``。

        Args:
            utterance_id: 编排层生成的唯一 ID（关联 reply/TTS/存储）。
            text: 待合成文本。
            priority: 优先级，越大越先播（默认 0：全部同级，行为等同普通 FIFO 队列）。
            ttl_ms: 多久之内没轮到就作废（0 = 不过期）。突发反应用：过时了不该再说。
            segment: 段落号：长段话拆成的几句共用一个。
            emotion: 这句话的情绪提示（``Emotion`` 枚举值字符串）；随句透传给 TTS。
            emotion_intensity: 情绪强度 0.0~1.0；与 ``emotion`` 成对出现。
            on_start: 轮到这句、交给 TTS 的那一刻调用一次（流式引擎此刻发
                ``tts.utterance.started``，字幕和口型也从这时开始）；用来记它实际
                排了多久，回调出错只记日志不挡播放。
            on_interrupted: 段落被插断、这句没播就作废时调用（参数是同段作废句拼起来的原文）；
                杂谈档用它记下她没说完的后半段，下一轮决定要不要补完。

        Returns:
            是否被接受；队列停用、或队满时新条目优先级最低被丢掉时返回 False。
        """
        if self._stopping.is_set():
            self._logger.warning(f"队列正在停止，拒绝入队: utterance_id={utterance_id}")
            await self._emit_dropped(utterance_id, "stopping_rejected")
            return False

        expires_at_ms = self._clock() + ttl_ms if ttl_ms > 0 else 0
        item = _UtteranceItem(
            utterance_id=utterance_id,
            text=text,
            priority=priority,
            expires_at_ms=expires_at_ms,
            segment=segment,
            emotion=emotion,
            emotion_intensity=emotion_intensity,
            on_start=on_start,
            on_interrupted=on_interrupted,
        )
        async with self._lock:
            interrupted = self._drop_interrupted_segment(item)
            for dropped_item in interrupted:
                await self._emit_dropped(dropped_item.utterance_id, "segment_interrupted")
            self._notify_interrupted(interrupted)
            if len(self._queue) >= self._max_queue:
                # 丢优先级最低里最旧的；全部同级时就是丢最旧（队首），即普通 FIFO 行为
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
        dropped_items: list[_UtteranceItem] = []
        async with self._lock:
            task = self._worker_task
            self._worker_task = None
            self._stopping.set()
            self._wakeup.set()  # 唤醒可能阻塞在 wait 的 worker
            if self._queue:
                # 残留未播放的 utterances 直接丢弃（不入 TTS）
                dropped_items = list(self._queue)
                dropped = len(dropped_items)
                self._total_dropped += dropped
                self._queue.clear()
                self._logger.warning(f"停止时丢弃未播放的 utterances: count={dropped}")

        for dropped_item in dropped_items:
            await self._emit_dropped(dropped_item.utterance_id, "queue_stopped")

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

    def _remove_lower_priority_locked(
        self,
        min_priority: int,
        *,
        segment_only: bool,
        exclude_segment: str = "",
        trigger_id: str = "interrupt",
    ) -> tuple[list[_UtteranceItem], list[_UtteranceItem], list[_UtteranceItem]]:
        """在持有 ``self._lock`` 的前提下移除队列中尚未开播的低优条目。

        移除规则：
        - ``segment_only=True``（段落插断 / ``enqueue`` 同段收尾）：只移除属于当前在播段落
          （``self._current_segment`` 非空且与 ``exclude_segment`` 不同）且优先级低于
          ``min_priority`` 的余句；
        - ``segment_only=False``（一级打断 ``interrupt_lower_priority``）：移除所有优先级低于
          ``min_priority`` 的待播条目，并按是否属于当前在播段落拆成两类分别上报原因。

        Returns:
            三元组 ``(all_removed, segment_dropped, priority_preempted)``，其中
            ``all_removed`` 保持原队列先后顺序。
        """
        has_active_segment = bool(self._current_segment) and (
            not exclude_segment or exclude_segment != self._current_segment
        )
        if segment_only and not has_active_segment:
            return [], [], []

        all_removed: list[_UtteranceItem] = []
        segment_dropped: list[_UtteranceItem] = []
        priority_preempted: list[_UtteranceItem] = []
        kept: Deque[_UtteranceItem] = deque()

        for queued in self._queue:
            is_current_seg = has_active_segment and queued.segment == self._current_segment
            should_drop = queued.priority < min_priority and (is_current_seg if segment_only else True)
            if not should_drop:
                kept.append(queued)
                continue
            all_removed.append(queued)
            if is_current_seg:
                segment_dropped.append(queued)
            else:
                priority_preempted.append(queued)

        if not all_removed:
            return [], [], []

        self._queue = kept
        self._total_interrupted += len(all_removed)
        if segment_dropped:
            self._logger.info(
                f"段落被插断，丢弃剩余 {len(segment_dropped)} 句: segment={self._current_segment}, 触发={trigger_id}"
            )
        if priority_preempted:
            self._logger.info(
                f"低优待播被高优抢占，清理 {len(priority_preempted)} 句: min_priority={min_priority}, 触发={trigger_id}"
            )
        return all_removed, segment_dropped, priority_preempted

    def _drop_interrupted_segment(self, item: _UtteranceItem) -> list[_UtteranceItem]:
        """正在播的段落被更高优先级的台词插进来：段落里还没播的句子作废，不在反应之后接着讲。

        段落还没开播时不算打断——急的先播，段落随后整段照常播。
        """
        _, segment_dropped, _ = self._remove_lower_priority_locked(
            item.priority,
            segment_only=True,
            exclude_segment=item.segment,
            trigger_id=item.utterance_id,
        )
        return segment_dropped

    async def _emit_interrupt_drops(
        self,
        segment_dropped: list[_UtteranceItem],
        priority_preempted: list[_UtteranceItem],
    ) -> None:
        """为被一级打断移除的条目分类发布 dropped 事件，并触发在播段落尾句回调。"""
        for dropped_item in segment_dropped:
            await self._emit_dropped(dropped_item.utterance_id, "segment_interrupted")
        for dropped_item in priority_preempted:
            await self._emit_dropped(dropped_item.utterance_id, "priority_preempted")
        if segment_dropped:
            self._notify_interrupted(segment_dropped)

    async def _interrupt_pending(self, min_priority: int, *, segment_only: bool) -> list[str]:
        """一级打断共用入口：清待播低优条目 -> 分类发布 dropped -> 唤醒背压等待方。"""
        if min_priority <= SPEECH_PRIORITY_NORMAL:
            return []
        async with self._lock:
            all_removed, segment_dropped, priority_preempted = self._remove_lower_priority_locked(
                min_priority,
                segment_only=segment_only,
            )
        if not all_removed:
            return []
        await self._emit_interrupt_drops(segment_dropped, priority_preempted)
        # 队列深度下降后立即唤醒等待方，让背压等待（wait_pending_capacity / wait_idle）立刻重判
        async with self._idle_cond:
            self._idle_cond.notify_all()
        return [item.text for item in all_removed]

    async def interrupt_lower_priority(self, min_priority: int) -> list[str]:
        """一级打断：高优发言到来时清理队列中尚未开始播放的低优条目，不掐断当前正在念的短句。

        在背压等待之前调用：
        - 属于当前正在播段落的余句沿用 ``"segment_interrupted"`` 发 ``tts.utterance.dropped``，
          并经 ``on_interrupted`` 合并记下尾句原文；
        - 其它排队中的低优独立句子（如排队中的常规解说 / 冷场独白）以 ``"priority_preempted"``
          发 ``tts.utterance.dropped`` 并计入 ``interrupted`` 统计。

        Returns:
            被清理条目的台词原文列表（保持原队列顺序）。
        """
        return await self._interrupt_pending(min_priority, segment_only=False)

    async def interrupt_segment(self, min_priority: int) -> list[str]:
        """更高优先级的发言到来时，先收住正在播的段落里还没念的后半句（向下兼容入口）。

        只移除属于当前正在播段落且优先级低于 ``min_priority`` 的余句，不碰其它排队中的独立句子；
        需要全队列清理低优待播请使用 :meth:`interrupt_lower_priority`。
        """
        return await self._interrupt_pending(min_priority, segment_only=True)

    async def hard_interrupt_playing(
        self,
        min_priority: int,
        *,
        max_victim_priority: int = SPEECH_PRIORITY_NORMAL,
    ) -> bool:
        """二级硬打断：极急事件到来时掐断当前正在播放的低优条目（默认仅限 P0 普通独白/解说）。

        触发条件（全部满足才执行）：
        1. ``min_priority >= SPEECH_PRIORITY_EMERGENCY``；
        2. worker 当前确有在播条目 ``_current_item``，且未处于关停或已发起硬打断状态；
        3. ``_current_item.priority <= max_victim_priority``（默认仅允许掐断 P0，
           绝不硬打断 P1 高光或 P2 观众回复）。

        触发后先顺带清理队列中 ``< min_priority`` 的低优待播条目，再取消单次 ``speak`` 子任务
        （不取消 worker 自身）；worker 捕获定向硬打断后发 ``hard_interrupted`` 并立刻续播下一条。

        Returns:
            是否成功发起二级硬打断。
        """
        if min_priority < SPEECH_PRIORITY_EMERGENCY:
            return False

        segment_dropped: list[_UtteranceItem] = []
        priority_preempted: list[_UtteranceItem] = []
        async with self._lock:
            if self._stopping.is_set():
                return False
            current = self._current_item
            if current is None or current.priority > max_victim_priority:
                return False
            if self._hard_interrupted_item is current:
                return False
            speak_task = self._current_speak_task
            if speak_task is not None and (speak_task.done() or not speak_task.cancel()):
                # 在播子任务已结束或刚好完成，不再发起硬打断
                return False

            # 标记该条目为定向硬打断，并顺带清理队内低优待播条目（此时 _current_segment 仍指向在播段落）
            self._hard_interrupted_item = current
            _, segment_dropped, priority_preempted = self._remove_lower_priority_locked(
                min_priority,
                segment_only=False,
                trigger_id="hard_interrupt",
            )

        if segment_dropped or priority_preempted:
            await self._emit_interrupt_drops(segment_dropped, priority_preempted)
            async with self._idle_cond:
                self._idle_cond.notify_all()

        self._logger.info(
            f"触发二级硬打断，掐断在播低优条目: utterance_id={current.utterance_id}, "
            f"victim_priority={current.priority}, min_priority={min_priority}"
        )
        return True

    def _notify_interrupted(self, dropped: list[_UtteranceItem]) -> None:
        """段落被插断后把作废的那几句原文交给段首带的回调（同段只通知一次）；回调出错只记日志。"""
        callback = next((item.on_interrupted for item in dropped if item.on_interrupted is not None), None)
        if callback is None:
            return
        try:
            callback("".join(item.text for item in dropped))
        except Exception as exc:  # noqa: BLE001 - 回调只是记尾句，出错不挡入队
            self._logger.warning(f"段落插断回调出错（已忽略）: {exc}", exc=True)

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

        return await self._wait_until(_is_idle, timeout_ms)

    async def wait_pending_capacity(self, max_pending: int, timeout_ms: Optional[int] = None) -> bool:
        """阻塞等待未播积压降到 ``max_pending`` 及以内（只看队列深度，不等当前句播完）。

        供发言管线的"欠一句"放行谓词（ADR-080）：播放中允许最多 ``max_pending``
        句先排队，规划器不必等嘴念完才有资格出声；0 等价于只等队列排空（当前句
        是否在播不看——那是 ``wait_idle`` 的语义）。超时返回 ``False`` 不抛异常。
        """

        def _within_capacity() -> bool:
            return len(self._queue) <= max_pending

        return await self._wait_until(_within_capacity, timeout_ms)

    async def _wait_until(self, predicate: Callable[[], bool], timeout_ms: Optional[int]) -> bool:
        """在"变闲"条件上等待谓词成立（worker 完成一条后唤醒等待方重判）。"""
        if predicate():
            return True
        timeout_s: Optional[float] = None if timeout_ms is None else timeout_ms / 1000.0
        try:
            async with self._idle_cond:
                await asyncio.wait_for(self._idle_cond.wait_for(predicate), timeout=timeout_s)
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
                expired_items: list[_UtteranceItem] = []
                async with self._lock:
                    if self._stopping.is_set():
                        return
                    item, expired_items = self._pop_fresh()
                    if item is None:
                        self._wakeup.clear()
                    else:
                        self._current_segment = item.segment
                        self._current_item = item
                        self._hard_interrupted_item = None
                        if not self._queue:
                            # 取完后队列为空，清除 wakeup 让下一次 enqueue 再唤醒
                            self._wakeup.clear()
                        # 置位与取件同临界区：wait_idle 的谓词读 (queue, processing)
                        # 二元组，同步段内事件循环不切换协程，不会观察到
                        # "队列已空但 processing 未置位"的假空闲窗口
                        self._processing = True
                for expired_item in expired_items:
                    await self._emit_dropped(expired_item.utterance_id, "expired")
                if item is None:
                    continue

                # 在锁外执行 speak（避免阻塞其他 enqueue）；若取件后、开播前已被定向硬打断，
                # 跳过 on_start 回调，避免未出声就误报开播
                if self._hard_interrupted_item is not item:
                    self._notify_start(item)
                try:
                    await self._dispatch(item)
                finally:
                    # 同步复位当前播放状态（无 await，事件循环内原子完成，避免关停时被二次取消打断）
                    if self._current_item is item:
                        self._current_item = None
                    if self._hard_interrupted_item is item:
                        self._hard_interrupted_item = None
                        self._current_segment = ""
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

    async def _emit_fuse_alert(self) -> None:
        """熔断触发旁路告警：发 core.error 让 WebUI 弹窗提示观众听不到语音。

        同一来源 5 分钟窗口内只发第一条（节流判定用注入时钟）；事件总线未
        注入或发布异常时只记日志，不影响队列继续运转。
        """
        now = self._clock()
        if now - self._last_fuse_alert_ms < ERROR_ALERT_THROTTLE_MS:
            self._logger.debug("熔断告警处于节流窗口内，跳过发布")
            return
        self._last_fuse_alert_ms = now
        message = (
            f"语音合成连续失败已熔断，观众听不到语音；每 {RENDER_FUSE_HALF_OPEN_MS // 1000}s 自动探路，恢复后自动续播"
        )
        if self._event_bus is None:
            return
        try:
            await self._event_bus.emit(
                CoreEvents.CORE_ERROR,
                CoreErrorPayload(message=message, data={"source": "tts"}),
                source="streamer_agent.utterance_queue",
            )
        except Exception as exc:  # noqa: BLE001 - 告警是旁路，不阻断队列
            self._logger.warning(f"发布熔断告警失败（已忽略）: err={exc}")

    def _notify_undelivered(self, utterance_id: str, status: str) -> None:
        """代码直连通知单条 utterance 未实际完播（``dropped`` / ``failed``）；旁路异常只记告警不阻断播放。"""
        callback = self._on_undelivered
        if callback is None or not utterance_id:
            return
        try:
            callback(utterance_id, status)
        except Exception as exc:  # noqa: BLE001 - 旁路回调失败不阻断队列
            self._logger.warning(
                f"未送达回调执行异常（已忽略）: utterance_id={utterance_id}, status={status}, err={exc}",
                exc=True,
            )

    async def _emit_failed(self, utterance_id: str, text: str, error_message: str) -> None:
        """这句到不了引擎，代发 ``tts.utterance.failed`` 让字幕兜底出字。

        ``emit`` 为后台扇出（事件总线自行 create_task 分发），不阻塞调用方。
        未注入事件总线、或发布失败时只记日志，不影响队列继续运转。
        """
        self._notify_undelivered(utterance_id, "failed")
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

    async def _emit_dropped(self, utterance_id: str, reason: str) -> None:
        """确知这句没送到观众面前（且未出字幕），发布 ``tts.utterance.dropped`` 供存储打没送达标记。"""
        self._notify_undelivered(utterance_id, "dropped")
        if self._event_bus is None:
            return
        payload = UtteranceDroppedPayload(
            utterance_id=utterance_id,
            reason=reason,
            timestamp_ms=self._clock(),
        )
        try:
            await self._event_bus.emit(
                CoreEvents.TTS_UTTERANCE_DROPPED,
                payload,
                source="streamer_agent.utterance_queue",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 丢弃事件不阻断队列
            self._logger.warning(f"发布 tts.utterance.dropped 失败（已忽略）: utterance_id={utterance_id}, err={exc}")

    def _pop_fresh(self) -> tuple[Optional[_UtteranceItem], list[_UtteranceItem]]:
        """取下一条还没过期的；过期的直接作废并返回，由外层发布丢弃事件。"""
        now = self._clock()
        expired: list[_UtteranceItem] = []
        while self._queue:
            item = self._queue.popleft()
            if item.expires_at_ms and item.expires_at_ms <= now:
                self._total_expired += 1
                self._logger.info(f"utterance 已过期，跳过: utterance_id={item.utterance_id}")
                expired.append(item)
                continue
            return item, expired
        return None, expired

    async def _handle_hard_interrupted(self, item: _UtteranceItem) -> None:
        """定向二级硬打断收尾：计入 interrupted 并发布 ``hard_interrupted``，不触碰超时与熔断计数。"""
        self._total_interrupted += 1
        self._logger.info(f"utterance 播放中被硬打断: utterance_id={item.utterance_id}")
        await self._emit_dropped(item.utterance_id, "hard_interrupted")

    async def _handle_render_timeout(self, item: _UtteranceItem) -> None:
        """单条渲染超时收尾：更新超时与熔断状态并代发 ``tts.utterance.failed``。"""
        self._total_timeouts += 1
        self._consecutive_render_timeouts += 1
        self._last_render_failure_ms = self._clock()
        if not self._render_fused and self._consecutive_render_timeouts >= RENDER_FUSE_THRESHOLD:
            self._render_fused = True
            self._logger.error(
                f"连续 {self._consecutive_render_timeouts} 句渲染超时，触发熔断："
                f"后续 utterance 不再渲染、直接代发 failed，每 {RENDER_FUSE_HALF_OPEN_MS}ms 放行一句探路"
            )
            await self._emit_fuse_alert()
        elif self._render_fused:
            self._logger.error(f"熔断半开探路句仍渲染超时，保持熔断并刷新计时: utterance_id={item.utterance_id}")
        self._logger.error(
            f"utterance 等待超时（>{self._render_timeout_ms}ms），放弃等待并继续下一条"
            f"（引擎取消清理后不会发声）: utterance_id={item.utterance_id}"
        )
        await self._emit_failed(item.utterance_id, item.text, f"render_timeout: 超过 {self._render_timeout_ms}ms")

    async def _dispatch(self, item: _UtteranceItem) -> None:
        """单条 utterance 的处理入口（speak + 超时保护 + 二级硬打断区分 + 错误兜底）。

        speak 可调用对象可能抛出异常（引擎 fail / 网络断等），本方法在外层
        捕获 asyncio.TimeoutError / 任何意外异常，保证 worker 永远能继续下一条；
        三路取消与超时严格区分：
        - 队列关停或 worker 自身被取消 → 取消 speak 子任务并上抛 ``CancelledError`` 退出 worker；
        - 定向二级硬打断 → 不计超时、不发 ``failed``，发布 ``hard_interrupted`` 后立刻续播下一条；
        - 渲染超时 → 计入 ``timeouts``、触发熔断判定并代发 ``tts.utterance.failed``。
        """
        timeout_s = self._render_timeout_ms / 1000.0
        # 熔断中：距最近一次渲染失败不满半开间隔，本句不启动渲染等待、立即代发
        # failed；满了就放行这一句探路（正常走渲染），成败由下方超时/成功分支处置
        if self._render_fused:
            if self._clock() - self._last_render_failure_ms < RENDER_FUSE_HALF_OPEN_MS:
                await self._emit_failed(
                    item.utterance_id,
                    item.text,
                    "render_timeout_fuse: 连续渲染超时熔断中，本句未尝试渲染",
                )
                return
            self._logger.info(f"熔断半开探路，放行一句尝试渲染: utterance_id={item.utterance_id}")

        # 窄窗口兜底：取件后、建立 speak 子任务前就被二级硬打断，直接按硬打断结算，不启动合成
        if self._hard_interrupted_item is item:
            if self._stopping.is_set():
                raise asyncio.CancelledError
            await self._handle_hard_interrupted(item)
            return

        # 把单次 speak 封装成独立子任务：二级硬打断只 cancel 该子任务，绝不杀死 worker 循环
        speak_task: asyncio.Task[None] = asyncio.create_task(
            self._speak(item.text, item.utterance_id, item.emotion, item.emotion_intensity),
            name=f"{self.__class__.__name__}.speak.{item.utterance_id}",
        )
        self._current_speak_task = speak_task

        try:
            await asyncio.wait_for(speak_task, timeout=timeout_s)
            self._total_processed += 1
            # 任何一句渲染成功都复位：连续计数清零，熔断关闭
            self._consecutive_render_timeouts = 0
            self._render_fused = False
        except asyncio.TimeoutError:
            # 硬打断收尾清理期间恰好撞上超时看门狗：仍按硬打断结算，不误记超时或熔断
            if self._hard_interrupted_item is item and not self._stopping.is_set():
                await self._handle_hard_interrupted(item)
                return
            await self._handle_render_timeout(item)
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            worker_cancelling = current_task is not None and current_task.cancelling() > 0
            if self._stopping.is_set() or worker_cancelling or self._hard_interrupted_item is not item:
                # 队列正在关停或 worker 自身被取消：确保在途 speak 子任务停播清理后上抛退出 worker
                if not speak_task.done():
                    speak_task.cancel()
                    try:
                        await speak_task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:  # noqa: BLE001 - 关停清理阶段异常只记日志
                        self._logger.debug(f"关停取消 speak 子任务时出现异常（已忽略）: {exc}")
                raise
            # 定向二级硬打断：仅取消了 speak 子任务，worker 继续存活并马上消费下一条紧急发言
            await self._handle_hard_interrupted(item)
        except Exception as exc:  # noqa: BLE001 - 防御性兜底
            if self._hard_interrupted_item is item and not self._stopping.is_set():
                self._logger.debug(
                    f"硬打断收尾期间 speak 抛出异常（按硬打断结算）: utterance_id={item.utterance_id}, err={exc}"
                )
                await self._handle_hard_interrupted(item)
                return
            # 轮到这句、speak 却抛了异常：先计数、打一条不带 traceback 的 ERROR
            # （引擎内部已打过带上下文的完整日志，连接类环境故障再打 traceback 会刷屏），
            # 再决定要不要替引擎补发 failed，最后 worker 继续下一条。
            self._total_errors += 1
            self._logger.error(f"speak 调用异常（已忽略，继续下一条）: utterance_id={item.utterance_id}, err={exc}")
            if is_failure_reported(exc):
                # 合成或播放中途失败：引擎已经先发过 failed（字幕已据此出字）再抛，
                # 这里不再补发事件，但仍需经代码直连通知上游该 utterance 未实际完播
                self._notify_undelivered(item.utterance_id, "failed")
                return
            # 引擎在发事件之前就失败了（典型：首句懒启动 setup() 抛错，如 Omni 引擎
            # 缺音色配置、Edge 引擎缺依赖或声卡初始化失败）：引擎一个事件都没发，
            # 队列补发一条 failed，字幕照常出字，她的话不会因为声音出不来而连字都看不到。
            # 没有标记的未知异常一律走这里——宁可多出一次字幕，也不能没有字幕
            await self._emit_failed(item.utterance_id, item.text, f"speak_error: {type(exc).__name__}: {exc}")
        finally:
            if self._current_speak_task is speak_task:
                self._current_speak_task = None


__all__ = [
    "UtteranceQueue",
    "SpeakCallable",
    "QUEUE_ENGINE_ID",
    "SPEECH_PRIORITY_NORMAL",
    "SPEECH_PRIORITY_HIGHLIGHT",
    "SPEECH_PRIORITY_AUDIENCE",
    "SPEECH_PRIORITY_EMERGENCY",
    "DEFAULT_MAX_QUEUE",
    "DEFAULT_RENDER_TIMEOUT_MS",
    "DEFAULT_WAIT_PLAYBACK_TIMEOUT_MS",
    "DEFAULT_MAX_PENDING",
    "RENDER_FUSE_THRESHOLD",
    "RENDER_FUSE_HALF_OPEN_MS",
]
