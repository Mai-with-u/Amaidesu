"""字幕播放事件跟随器（字幕源 = TTS 播放事件）

字幕"一源 → 三面"的播放对齐模式：TTS 引擎在位时，字幕跟随
``tts.utterance.*`` 生命周期——``started`` 把 speech_text 按停顿标点
（逗号/分号/句末标点，顿号与冒号不切、引号内不切）切段、
先显示第一段，出声后再把上屏文本切到下一段；``failed`` 照常显示该句全文
（无声音也要给文本，原文由 ``speech_text`` 必选字段承载，不分段）；
``finished`` 清空。与实际播放对齐：队列丢最旧的台词自然不显示。

估算时钟的零点锚在"真正出声"：``started`` 只切段并显示第一段，估算兜底
定时**不起跑**——``tts.utterance.playback_started``（首个音频块写进声卡，
播放路径分接发布）到达才把零点锚到该时刻并起跑估算。流式引擎的 started
在合成前发布，从 started 到真正出声隔着整段合成首块延迟，先行起跑会让
估算跑在出声之前（字幕播完声音才响）；等出声再起跑则任何延迟下估算都与
听感对齐。播放路径未挂分接（或整句无声）时该事件缺席，估算不起跑、字幕
停在第一段，由 ``failed``（显全文）与 ``finished``（清空）兜底收尾。

段切换的信号有两路，但都受两道约束：最短段显示时长（``_MIN_SEGMENT_SHOW_MS``：
当前段上屏不足该时长不切，防细段闪切）与估算下限闸门（``_BOUNDARY_EARLY_
TOLERANCE_MS``：事件比估算切换时刻早到超过容差不切）：

- **句边界事件**（``tts.utterance.sentence_boundary``）：播放路径静音检测
  检出停顿间隙的时刻发布，与观众听到的停顿天然同步，优先于定时。停顿数
  与文本段数不保证一致——GSV 等按标点分块合成的引擎停顿偏密（顿号处、
  凑块处也停），按序硬配会把文本段提前耗尽、字幕跑在语音前面，故事件须
  过估算下限闸门：早于估算的碎停顿忽略（段不消耗），晚于估算的真实长停
  顿照常采纳，估算与事件各管自己准的那半；
- **估算兜底定时**：防"检测不工作导致字幕卡在第一段"——``duration_ms``
  可用（全量引擎）按出声窗口的字数占比内插，不可用（流式引擎）按每字
  约 250ms 外推（语速由已采纳边界的实测时刻在线校准），两者都不低于最
  短段显示时长。定时到点但边界事件还没到 → 照切（估算节奏）；边界事件
  先到 → 取消定时。检测工作 = 真实同步，检测失效 = 估算节奏，任何情况
  下观众都能看到全部文本。

utterance 结束（finished）后其迟到事件按 utterance_id 不匹配丢弃。

引用弹幕（本句在回复谁的哪条发言）不随 ``tts.utterance.*`` 走：跟随器
额外订阅 ``streamer.speech``，按 utterance_id 记住该句的回复目标
（有界映射，旧条目按先后挤出），显示每段字幕时反查带上——杂谈档分段
连播时派生编号（基编号加 ``_pN`` 后缀）回退基编号查。发言事件先于语音
合成发布，而合成首块延迟远大于事件总线一次后台派发，"先记后查"时序
天然成立。派发直出模式不经本模块，引用随 ``show`` 调用显式携带。

择源在装配期完成（有 TTS 引擎 → 本跟随器；无 → 编排层派发直出），
两种模式互斥，非两套源同时跑。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtterancePlaybackStartedPayload,
    UtteranceSentenceBoundaryPayload,
    UtteranceStartedPayload,
)
from src.modules.logging import ModuleLogger
from src.modules.utils.time_utils import now_ms

from .protocol import ReplyQuote

# 停顿标点集合：句末标点（含叠用省略号/叠用叹号等）与句内停顿（逗号/分号）；
# 切分点下沉到逗号级——真机上按句末标点切段过粗（"一逗到底"句整句不换行），
# 且播放路径静音检测对逗号停顿同样报边界，段数须与边界数同量级；段尾标点不显示
_CLAUSE_SPLIT_PATTERN = re.compile(r"[，,；。！？…]+")
# 引号配对表：开引号 → 对应闭引号；引号内的停顿标点不切（对话内的逗号不是字幕分段的停顿）
_QUOTE_CLOSERS = {"“": "”", "『": "』", "「": "」", '"': '"'}
# 最短段显示时长：逗号级切分后单段变短，切早了画面闪切；
# 估算定时与边界事件消费共用此下限
_MIN_SEGMENT_SHOW_MS = 800
# 流式引擎无 duration_ms 时的兜底语速估算：每字约 250ms
_FALLBACK_MS_PER_CHAR = 250
# 边界事件闸门容差：事件早于估算切换时刻超过此值视为合成器分块的碎停顿，
# 不推进段（密停顿按序硬配会把文本提前耗尽，字幕跑在语音前面）
_BOUNDARY_EARLY_TOLERANCE_MS = 800
# 语速校准的起算字数：样本太少（首段常只有两三个字）时单段时长换算的语速
# 方差过大，攒够字数才开始用实测语速替换兜底值
_RATE_CALIBRATION_MIN_CHARS = 6
# 校准语速的上下限：极端样本（异常拖长音的段）不把估算拉到离谱
_MS_PER_CHAR_MIN = 80
_MS_PER_CHAR_MAX = 500
# 回复目标映射的容量上限：只服务"当前正在显示的字幕"，超过即挤掉最早登记的；
# 杂谈档分段派生编号共享基编号条目，不占额外容量
_QUOTE_CACHE_MAX = 64
# 杂谈档分段连播的派生编号后缀（基编号加 _p2 / _p3 …），反查引用时剥掉
_PART_SUFFIX_PATTERN = re.compile(r"_p\d+$")


def split_subtitle_segments(text: str) -> List[str]:
    """按停顿标点把整句字幕切段；段尾标点不显示。

    切分点：逗号/分号/句末标点；顿号（并列）、冒号（引出）不是停顿不切；
    引号内的标点不切（引号配对栈跟踪，引号整体随所在段显示）。
    无任何停顿标点的文本返回单段（整句），调用方按单段处理即维持原行为。
    """
    segments: List[str] = []
    buf: List[str] = []
    quote_closers: List[str] = []
    for ch in text:
        closer = _QUOTE_CLOSERS.get(ch)
        if closer is not None:
            quote_closers.append(closer)
        elif quote_closers and ch == quote_closers[-1]:
            quote_closers.pop()
        if not quote_closers and _CLAUSE_SPLIT_PATTERN.match(ch):
            stripped = "".join(buf).strip()
            if stripped:
                segments.append(stripped)
            buf.clear()
            continue
        buf.append(ch)
    stripped = "".join(buf).strip()
    if stripped:
        segments.append(stripped)
    return segments


class _SegmentSchedule:
    """一句话的分段会话：当前段号与下一段切换时刻的估算。

    切换时刻估算规则：``duration_ms`` 可用时按"出声窗口 × 段起点字数占比"
    内插；不可用（流式引擎）按每字语速外推，语速初值 250ms/字、由已切换
    边界的实测时刻在线校准（见 :meth:`note_segment_started`）。估算值不低
    于最短段显示时长（逗号级细段按字数或占比推会低于可读下限，定时切早了
    闪切）；闸门比对用的 :meth:`next_switch_time_ms` 不做此托底。
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
        # 流式估算的语速（毫秒/字）与在线校准的累计样本
        self.ms_per_char = float(_FALLBACK_MS_PER_CHAR)
        self._calibrated_chars = 0
        self._calibrated_ms = 0
        # 当前段起讲时刻（出声时钟）：playback_started 时为首段起讲，其后
        # 每次切换更新；duration 不可用时据此外推下一段切换时刻
        self.boundary_ms: Optional[int] = None

    def next_delay_ms(self, now_ms_value: int, started_ms: int) -> int:
        """距下一段兜底切换时刻的毫秒数；已无下一段时返回 0（调用方不再建定时）。"""
        est = self.next_switch_time_ms(now_ms_value, started_ms)
        if est is None:
            return 0
        return max(est - now_ms_value, _MIN_SEGMENT_SHOW_MS)

    def next_switch_time_ms(self, now_ms_value: int, started_ms: int) -> Optional[int]:
        """下一段切换时刻的估算（绝对毫秒时刻）；无下一段或尚未出声时返回 None。

        与 :meth:`next_delay_ms` 同源，但不做最短段显示时长托底——闸门要比
        对"边界事件比估算早到了多少"，托底值会掩盖这个差值。
        """
        next_index = self.current_index + 1
        if next_index >= len(self.segments):
            return None
        if self.duration_ms:
            total = self.prefix[-1]
            if total <= 0:
                return None
            # 本段讲完即切下一段：段起点的字数占比 × 出声窗口
            return started_ms + int(self.duration_ms * self.prefix[next_index] / total)
        if self.boundary_ms is None:
            return None
        chars_to_boundary = self.prefix[next_index] - self.prefix[self.current_index]
        return self.boundary_ms + int(chars_to_boundary * self.ms_per_char)

    def advance(self) -> bool:
        """切到下一段；返回是否确实有下一段（False = 文本段已耗尽）。"""
        if self.current_index + 1 >= len(self.segments):
            return False
        self.current_index += 1
        return True

    def note_segment_started(self, at_ms: int) -> None:
        """记录当前段起讲时刻（出声时钟）；顺带用刚讲完的一段校准语速。

        ``duration_ms`` 可用时不校准（估算走窗口占比，不依赖语速）；流式
        路径用已讲完段落的实测耗时折算每字毫秒，累计字数过少（单段字数
        太少时方差大）或超出上下限时保持/截断，防异常样本把估算拉偏。
        """
        if self.boundary_ms is not None and self.current_index > 0 and not self.duration_ms:
            spoken_ms = at_ms - self.boundary_ms
            spoken_chars = self.prefix[self.current_index] - self.prefix[self.current_index - 1]
            if spoken_ms > 0 and spoken_chars > 0:
                self._calibrated_ms += spoken_ms
                self._calibrated_chars += spoken_chars
                if self._calibrated_chars >= _RATE_CALIBRATION_MIN_CHARS:
                    self.ms_per_char = min(
                        max(self._calibrated_ms / self._calibrated_chars, float(_MS_PER_CHAR_MIN)),
                        float(_MS_PER_CHAR_MAX),
                    )
        self.boundary_ms = at_ms


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
        # 当前段上屏时刻：最短段显示时长的计时锚点
        self._last_switch_ms = 0
        self._cancel_timer: Optional[Callable[[], None]] = None
        # utterance_id → 该句回复的观众发言；streamer.speech 订阅登记，
        # 显示每段字幕时反查带上（容量超限挤最早条目）
        self._reply_quotes: Dict[str, ReplyQuote] = {}

    def remember_quote(self, utterance_id: str, quote: ReplyQuote) -> None:
        """登记一句发言的回复目标（streamer.speech 订阅回调的落点）。

        按 utterance_id 覆盖登记；容量超限时按登记先后挤出最早条目。
        """
        if utterance_id in self._reply_quotes:
            del self._reply_quotes[utterance_id]
        self._reply_quotes[utterance_id] = quote
        while len(self._reply_quotes) > _QUOTE_CACHE_MAX:
            oldest = next(iter(self._reply_quotes))
            del self._reply_quotes[oldest]

    def quote_for(self, utterance_id: str) -> Optional[ReplyQuote]:
        """反查一句字幕的回复目标；查不到返回 None（不渲染引用行）。

        分段连播的派生编号（基编号加 ``_pN`` 后缀）在基编号下查不到时
        剥掉后缀回退基编号——几句共用同一次决策的回复目标。
        """
        quote = self._reply_quotes.get(utterance_id)
        if quote is not None:
            return quote
        base_id = _PART_SUFFIX_PATTERN.sub("", utterance_id)
        if base_id != utterance_id:
            return self._reply_quotes.get(base_id)
        return None

    async def on_started(self, payload: UtteranceStartedPayload) -> None:
        self._cancel_session()
        segments = split_subtitle_segments(payload.speech_text)
        if not segments:
            return
        await self._show(segments[0], payload.utterance_id, self.quote_for(payload.utterance_id))
        self._last_switch_ms = self._now_ms()
        if len(segments) == 1:
            return
        self._session = _SegmentSchedule(payload.utterance_id, segments, payload.duration_ms)
        # 估算定时此刻不起跑：等 playback_started 把零点锚到真正出声再排
        self._started_ms = self._now_ms()

    async def on_failed(self, payload: UtteranceFailedPayload) -> None:
        # 运行期逐句降级：合成失败该句照常显示全文（无声音也要给文本），不分段
        self._cancel_session()
        await self._show(payload.speech_text, payload.utterance_id, self.quote_for(payload.utterance_id))

    async def on_finished(self, payload: UtteranceFinishedPayload) -> None:
        self._cancel_session()
        await self._clear()

    async def on_boundary(self, payload: UtteranceSentenceBoundaryPayload) -> None:
        session = self._session
        if session is None or payload.utterance_id != session.utterance_id:
            return  # 无会话 / 已结束 utterance 的迟到事件：丢弃
        if self._now_ms() - self._last_switch_ms < _MIN_SEGMENT_SHOW_MS:
            return  # 当前段上屏不足最短时长：静音检测对逗号停顿也报边界，早到的忽略不耗段
        # 估算下限闸门：早于估算切换时刻超过容差的停顿是合成器分块的碎停顿
        # （GSV 等按标点分块合成的引擎，停顿数多于文本段数，按序硬配会把
        # 文本提前耗尽、字幕跑在语音前面），不推进段，估算定时到点照切；
        # 晚于估算的真实长停顿不受影响，仍按事件精确对齐
        est = session.next_switch_time_ms(payload.timestamp_ms, self._started_ms)
        if est is not None and payload.timestamp_ms < est - _BOUNDARY_EARLY_TOLERANCE_MS:
            return
        self._disarm_timer()
        await self._advance(session)

    async def on_playback_started(self, payload: UtterancePlaybackStartedPayload) -> None:
        session = self._session
        if session is None or payload.utterance_id != session.utterance_id:
            return  # 无会话 / 已结束 utterance 的迟到事件：丢弃
        # 估算时钟零点 = 真正出声时刻：合成首块延迟不计入估算窗口，
        # 此刻才起跑兜底估算；句边界事件其后仍优先接管
        self._started_ms = self._now_ms()
        session.note_segment_started(self._started_ms)
        self._disarm_timer()
        self._arm_fallback_timer()

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
        await self._show(
            session.segments[session.current_index],
            session.utterance_id,
            self.quote_for(session.utterance_id),
        )
        self._last_switch_ms = self._now_ms()
        # 新段起讲时刻与本会话其他时刻同钟（控制器时钟）；事件检测时刻与
        # 此处的毫秒级差由语速校准的自然平滑吸收
        session.note_segment_started(self._now_ms())
        self._arm_fallback_timer()

    def _cancel_session(self) -> None:
        self._disarm_timer()
        self._session = None

    async def _show(self, text: str, utterance_id: str, quote: Optional[ReplyQuote] = None) -> None:
        try:
            await self._subtitle_service.show(text, utterance_id, quote)
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
) -> Optional[Tuple[Any, Any, Any, Any, Any, Any]]:
    """订阅 ``tts.utterance.*`` 驱动字幕分段显示/清空 + ``streamer.speech``
    登记回复目标；返回 handler 组供退订。

    handler 内部对 ``SubtitleService.show`` / ``clear`` 与分段会话做异常隔离
    （字幕是装饰性路径，异常吞掉记日志，不影响 TTS 生命周期广播的其他消费者）。

    Args:
        event_bus: 事件总线；None 或 subtitle_service 为 None 时不绑定。
        subtitle_service: 字幕服务（``show(text, utterance_id, reply_quote)`` /
            ``clear()``）。
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

    async def on_playback_started(event_name: str, payload: UtterancePlaybackStartedPayload, source: str) -> None:
        await controller.on_playback_started(payload)

    async def on_speech(event_name: str, payload: StreamerSpeechPayload, source: str) -> None:
        user_name = payload.reply_to_user_name
        text = payload.reply_to_text
        if not user_name or not text:
            return  # 主动发言 / 未解析到回复目标：无引用可登记
        controller.remember_quote(payload.utterance_id, ReplyQuote(user_name=user_name, text=text))

    event_bus.on(CoreEvents.TTS_UTTERANCE_STARTED, on_started, model_class=UtteranceStartedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_FAILED, on_failed, model_class=UtteranceFailedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_FINISHED, on_finished, model_class=UtteranceFinishedPayload)
    event_bus.on(CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, on_boundary, model_class=UtteranceSentenceBoundaryPayload)
    event_bus.on(
        CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED, on_playback_started, model_class=UtterancePlaybackStartedPayload
    )
    event_bus.on(CoreEvents.STREAMER_SPEECH, on_speech, model_class=StreamerSpeechPayload)
    logger.debug(
        "已订阅 tts.utterance.* → 字幕跟随播放（started 分段显示第一段，playback_started 锚定出声时刻"
        "起跑估算，句边界事件与估算定时双路切换，failed 显示全文，finished 清空）；"
        "streamer.speech → 登记回复目标随字幕显示引用行"
    )
    return (on_started, on_failed, on_finished, on_boundary, on_playback_started, on_speech)


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
            CoreEvents.TTS_UTTERANCE_PLAYBACK_STARTED,
            CoreEvents.STREAMER_SPEECH,
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
