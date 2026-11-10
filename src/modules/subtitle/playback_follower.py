"""字幕播放事件跟随器（字幕源 = TTS 播放事件）

字幕"一源 → 三面"的播放对齐模式：TTS 引擎在位时，字幕跟随
``tts.utterance.*`` 生命周期——``started`` 把 speech_text 按句末标点切段、
先显示第一段，随后把上屏文本切到下一段；``failed`` 照常显示该句全文
（无声音也要给文本，原文由 ``speech_text`` 必选字段承载，不分段）；
``finished`` 清空。与实际播放对齐：队列丢最旧的台词自然不显示。

段切换的信号有两路，任一路先到即切：

- **句边界事件**（``tts.utterance.sentence_boundary``）：播放路径静音检测
  检出句间隙的时刻发布，与观众听到的停顿天然同步，优先于定时；
- **估算兜底定时**：防"检测不工作导致字幕卡在第一段"——``duration_ms``
  可用（全量引擎）按剩余窗口的字数占比推，不可用（流式引擎）按每字约
  250ms 估算。定时到点但边界事件还没到 → 照切（估算节奏）；边界事件先到
  → 取消定时。检测工作 = 真实同步，检测失效 = 估算节奏，任何情况下观众
  都能看到全部文本。

段数与边界数不齐不致命：多切按实际事件走，文本段耗尽后忽略多余事件；
utterance 结束（finished）后其迟到事件按 utterance_id 不匹配丢弃。

择源在装配期完成（有 TTS 引擎 → 本跟随器；无 → 编排层派发直出），
两种模式互斥，非两套源同时跑。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, List, Optional, Tuple

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceSentenceBoundaryPayload,
    UtteranceStartedPayload,
)
from src.modules.logging import ModuleLogger
from src.modules.utils.time_utils import now_ms

# 句末标点集合（含叠用省略号/叠用叹号等）；段尾标点不显示
_SENTENCE_END_PATTERN = re.compile(r"[。！？…]+")
# 流式引擎无 duration_ms 时的兜底语速估算：每字约 250ms
_FALLBACK_MS_PER_CHAR = 250


def split_subtitle_segments(text: str) -> List[str]:
    """按句末标点把整句字幕切段；段尾标点不显示。

    无句末标点的文本返回单段（整句），调用方按单段处理即维持原行为。
    """
    segments: List[str] = []
    for part in _SENTENCE_END_PATTERN.split(text):
        stripped = part.strip()
        if stripped:
            segments.append(stripped)
    return segments


class _SegmentSchedule:
    """一句话的分段会话：当前段号与下一兜底切换时刻的估算。

    切换时刻估算规则：``duration_ms`` 可用时按"剩余窗口 × 剩余段字数占比"
    推（每次切换后以当前时刻为锚重排，早切/晚切后后续估算随之收敛）；
    不可用时按每字约 250ms 估算。
    """

    def __init__(self, utterance_id: str, segments: List[str], duration_ms: Optional[int]) -> None:
        self.utterance_id = utterance_id
        self.segments = segments
        self.duration_ms = duration_ms
        self.current_index = 0
        # 各段的字符数前缀和：prefix[i] = 第 i 段之前的字符总数
        self.prefix = [0] * (len(segments) + 1)
        for i, segment in enumerate(segments):
            self.prefix[i + 1] = self.prefix[i] + len(segment)

    def next_delay_ms(self, now_ms_value: int, started_ms: int) -> int:
        """距下一段兜底切换时刻的毫秒数；已无下一段时返回 0（调用方不再建定时）。"""
        next_index = self.current_index + 1
        if next_index >= len(self.segments):
            return 0
        chars_to_next = self.prefix[next_index + 1] - self.prefix[next_index]
        if self.duration_ms:
            remaining_total = max(0, started_ms + self.duration_ms - now_ms_value)
            chars_remaining = self.prefix[-1] - self.prefix[self.current_index]
            if chars_remaining > 0 and remaining_total > 0:
                return int(remaining_total * chars_to_next / chars_remaining)
        return chars_to_next * _FALLBACK_MS_PER_CHAR

    def advance(self) -> bool:
        """切到下一段；返回是否确实有下一段（False = 文本段已耗尽）。"""
        if self.current_index + 1 >= len(self.segments):
            return False
        self.current_index += 1
        return True


class _LoopScheduler:
    """默认定时器：asyncio 事件循环 call_later；返回取消函数。"""

    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> Callable[[], None]:
        handle = asyncio.get_running_loop().call_later(max(0.0, delay_ms / 1000), callback)
        return handle.cancel


class _PlaybackSubtitleController:
    """字幕分段状态的管理面：会话登记、段切换（事件/定时双路）、清理。"""

    def __init__(
        self,
        subtitle_service: Any,
        logger: ModuleLogger,
        *,
        now_ms_fn: Callable[[], int] = now_ms,
        scheduler: Any = None,
    ) -> None:
        self._subtitle_service = subtitle_service
        self._logger = logger
        self._now_ms = now_ms_fn
        self._scheduler = scheduler if scheduler is not None else _LoopScheduler()
        self._session: Optional[_SegmentSchedule] = None
        self._started_ms = 0
        self._cancel_timer: Optional[Callable[[], None]] = None

    async def on_started(self, payload: UtteranceStartedPayload) -> None:
        self._cancel_session()
        segments = split_subtitle_segments(payload.speech_text)
        if not segments:
            return
        await self._show(segments[0], payload.utterance_id)
        if len(segments) == 1:
            return
        self._session = _SegmentSchedule(payload.utterance_id, segments, payload.duration_ms)
        self._started_ms = self._now_ms()
        self._arm_fallback_timer()

    async def on_failed(self, payload: UtteranceFailedPayload) -> None:
        # 运行期逐句降级：合成失败该句照常显示全文（无声音也要给文本），不分段
        self._cancel_session()
        await self._show(payload.speech_text, payload.utterance_id)

    async def on_finished(self, payload: UtteranceFinishedPayload) -> None:
        self._cancel_session()
        await self._clear()

    async def on_boundary(self, payload: UtteranceSentenceBoundaryPayload) -> None:
        session = self._session
        if session is None or payload.utterance_id != session.utterance_id:
            return  # 无会话 / 已结束 utterance 的迟到事件：丢弃
        self._disarm_timer()
        await self._advance(session)

    def _arm_fallback_timer(self) -> None:
        """按当前会话重排兜底定时；已耗尽段时不再建。"""
        session = self._session
        if session is None:
            return
        delay_ms = session.next_delay_ms(self._now_ms(), self._started_ms)
        if delay_ms <= 0:
            return

        def _fire() -> None:
            # 定时到点但句边界事件还没到：照切（估算兜底节奏）；
            # 切段要 await 字幕写入，调度回事件循环异步执行
            if self._session is session:
                asyncio.get_running_loop().create_task(self._advance(session))

        self._cancel_timer = self._scheduler.schedule(delay_ms, _fire)

    def _disarm_timer(self) -> None:
        if self._cancel_timer is not None:
            try:
                self._cancel_timer()
            except Exception as exc:  # noqa: BLE001 - 装饰性路径边界
                self._logger.debug(f"字幕兜底定时取消失败（已忽略）: {exc}")
            self._cancel_timer = None

    async def _advance(self, session: _SegmentSchedule) -> None:
        if not session.advance():
            self._cancel_session()
            return
        await self._show(session.segments[session.current_index], session.utterance_id)
        self._arm_fallback_timer()

    def _cancel_session(self) -> None:
        self._disarm_timer()
        self._session = None

    async def _show(self, text: str, utterance_id: str) -> None:
        try:
            await self._subtitle_service.show(text, utterance_id)
        except Exception as exc:  # noqa: BLE001 - 装饰性路径边界
            self._logger.warning(f"字幕 show 异常（已忽略）: utterance_id={utterance_id}, err={exc}")

    async def _clear(self) -> None:
        try:
            await self._subtitle_service.clear()
        except Exception as exc:  # noqa: BLE001 - 装饰性路径边界
            self._logger.warning(f"字幕 clear 异常（已忽略）: err={exc}")


def bind_playback_subtitle(
    event_bus: Optional[EventBus],
    subtitle_service: Any,
    logger: ModuleLogger,
    *,
    now_ms_fn: Callable[[], int] = now_ms,
    scheduler: Any = None,
) -> Optional[Tuple[Any, Any, Any, Any]]:
    """订阅 ``tts.utterance.*`` 驱动字幕分段显示/清空；返回 handler 组供退订。

    handler 内部对 ``SubtitleService.show`` / ``clear`` 与分段会话做异常隔离
    （字幕是装饰性路径，异常吞掉记日志，不影响 TTS 生命周期广播的其他消费者）。

    Args:
        event_bus: 事件总线；None 或 subtitle_service 为 None 时不绑定。
        subtitle_service: 字幕服务（``show(text, utterance_id)`` / ``clear()``）。
        logger: 项目门面 logger。
        now_ms_fn: 毫秒时刻入口（可注入假时钟供测试）。
        scheduler: 段切换兜底定时器（``schedule(delay_ms, callback) → cancel``）；
            缺省用 asyncio 事件循环定时。
    """
    if event_bus is None or subtitle_service is None:
        return None
    controller = _PlaybackSubtitleController(subtitle_service, logger, now_ms_fn=now_ms_fn, scheduler=scheduler)

    async def on_started(event_name: str, payload: UtteranceStartedPayload, source: str) -> None:
        await controller.on_started(payload)

    async def on_failed(event_name: str, payload: UtteranceFailedPayload, source: str) -> None:
        await controller.on_failed(payload)

    async def on_finished(event_name: str, payload: UtteranceFinishedPayload, source: str) -> None:
        await controller.on_finished(payload)

    async def on_boundary(event_name: str, payload: UtteranceSentenceBoundaryPayload, source: str) -> None:
        await controller.on_boundary(payload)

    event_bus.on(CoreEvents.TTS_UTTERANCE_STARTED, on_started, model_class=UtteranceStartedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_FAILED, on_failed, model_class=UtteranceFailedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_FINISHED, on_finished, model_class=UtteranceFinishedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, on_boundary, model_class=UtteranceSentenceBoundaryPayload)
    logger.debug(
        "已订阅 tts.utterance.* → 字幕跟随播放（started 分段显示，句边界事件与估算定时双路切换，"
        "failed 显示全文，finished 清空）"
    )
    return (on_started, on_failed, on_finished, on_boundary)


def unbind_playback_subtitle(
    event_bus: Optional[EventBus],
    handlers: Any,
    logger: Optional[ModuleLogger] = None,
) -> None:
    """退订播放字幕跟随器（shutdown 时调用；未绑定静默）。"""
    if event_bus is None or handlers is None:
        return
    for event_name, handler in zip(
        (
            CoreEvents.TTS_UTTERANCE_STARTED,
            CoreEvents.TTS_UTTERANCE_FAILED,
            CoreEvents.TTS_UTTERANCE_FINISHED,
            CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY,
        ),
        handlers,
        strict=True,
    ):
        try:
            event_bus.off(event_name, handler)
        except Exception as exc:  # noqa: BLE001 - 退订失败不阻断清理
            if logger is not None:
                logger.debug(f"tts.utterance.* 字幕退订失败（已忽略）: {exc}")


__all__ = ["bind_playback_subtitle", "unbind_playback_subtitle", "split_subtitle_segments"]
