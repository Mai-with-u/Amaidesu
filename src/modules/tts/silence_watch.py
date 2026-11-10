"""播放路径静音间隙检测（字幕拆句的句边界信号源）

TTS 音频写到声卡的同一时刻顺带算窗口能量：检出 ≥ 阈值（约 200ms）的
静音间隙、且此前确有发声（开头静音不误触发），就在那一刻发一个句边界
事件；字幕 follower 收到事件把上屏文本切到下一段。挂在播放路径（音频
分接 ``AudioSink``）而非合成路径的原因：流式引擎在 started 事件发出时
音频还没合成完，无法预计算边界；播放路径的递入节奏与观众听感天然同步，
且全量/流式两种播法共用同一分接点，接线只此一处。

两个部件：

- :class:`SilenceGapDetector`：纯状态机，逐块喂 PCM（归一化 → 定长帧
  RMS → 有声/静音判定），返回"本块内是否跨过句间隙"。无副作用，异常
  由调用方兜底。
- :class:`SentenceBoundarySink`：``AudioSink`` 协议形状的分接接收方。
  ``feed`` 是同步接口（不得拖慢播放），检出边界后经 ``call_soon_threadsafe``
  把事件发布调度回事件循环异步执行——流式播法的 ``feed`` 在工作线程被调，
  事件总线只能在事件循环线程上操作。检测与发布的任何异常都就地吞掉记
  日志，绝不影响音频播放本身。

会话形状（``start → feed×N → stop``）即 utterance 生命周期：start 复位
检测器并记录 utterance_id，stop 复位一切状态；utterance 结束后其迟到
状态自然丢弃。
"""

from __future__ import annotations

import asyncio
from typing import Optional

import numpy as np

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import UtteranceSentenceBoundaryPayload
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms

# 判为静音间隙的最短时长；中文 TTS 句间停顿普遍在 200ms 上下，短于此的
# 音节间隙（声母爆破后的低能量段）不应触发切句
DEFAULT_MIN_GAP_MS = 200
# 能量判定的帧长；太长会把短促发声糊进静音帧，太短则单帧抖动敏感
DEFAULT_FRAME_MS = 10
# 帧 RMS 低于此值判静音。输入先归一化到 [-1, 1]（整型除以满幅），正常
# 语音帧 RMS 远高于此、TTS 间隙帧接近 0，阈值只需区分两者
DEFAULT_SILENCE_THRESHOLD = 0.01


class SilenceGapDetector:
    """逐块喂 PCM 的静音间隙状态机；单次间隙只报一次，重新发声后重新武装。

    内部把输入按定长帧切窗算 RMS，维护"是否已有发声帧 + 当前连续静音帧
    数"。连续静音攒够 ``min_gap_ms`` 且此前出现过发声帧时返回 True 一次；
    之后的静音帧不再重复报，直到下一次发声帧把它重新武装——开头静音、
    句尾收尾静音都不会造成误报或重复报。
    """

    def __init__(
        self,
        *,
        min_gap_ms: int = DEFAULT_MIN_GAP_MS,
        frame_ms: int = DEFAULT_FRAME_MS,
        silence_threshold: float = DEFAULT_SILENCE_THRESHOLD,
    ) -> None:
        self._min_gap_ms = min_gap_ms
        self._frame_ms = frame_ms
        self._silence_threshold = silence_threshold
        self._leftover = np.empty(0, dtype=np.float64)
        self.reset()

    def reset(self) -> None:
        """回到尚未发声、无连续静音的初始状态（新 utterance 开始时调用）。"""
        self._has_voiced = False
        self._silent_frames = 0
        self._gap_reported = False

    def feed(self, samples: "np.ndarray", sample_rate: int, channels: int = 1) -> bool:
        """喂入一个 PCM 块；返回本块内是否跨过了句间隙（最多一次）。

        Args:
            samples: PCM 样本（一维或多维数组；多维按行摊平后取声道均值），
                整型按满幅归一化，浮点原样使用。
            sample_rate: 采样率（Hz）；非正数时忽略本块。
            channels: 声道数；小于 1 视为 1。

        Returns:
            本块内是否检出一次句间隙。
        """
        if sample_rate <= 0:
            return False
        channels = max(1, channels)
        mono = self._to_mono_float(samples, channels)
        if mono.size == 0:
            return False

        frame_samples = max(1, int(sample_rate * self._frame_ms / 1000))
        # 帧可能跨块边界：上一块的余量先拼回来，凑满整帧的处理，余量留待下块
        data = np.concatenate((self._leftover, mono)) if self._leftover.size else mono
        frame_count = data.size // frame_samples
        if frame_count == 0:
            self._leftover = data
            return False
        self._leftover = data[frame_count * frame_samples :].copy()

        frames = data[: frame_count * frame_samples].reshape(frame_count, frame_samples)
        rms = np.sqrt(np.mean(np.square(frames), axis=1))
        crossed = False
        for is_silent in rms < self._silence_threshold:
            if is_silent:
                self._silent_frames += 1
                if (
                    self._has_voiced
                    and not self._gap_reported
                    and self._silent_frames * self._frame_ms >= self._min_gap_ms
                ):
                    self._gap_reported = True
                    crossed = True
            else:
                self._has_voiced = True
                self._silent_frames = 0
                self._gap_reported = False
        return crossed

    @staticmethod
    def _to_mono_float(samples: "np.ndarray", channels: int) -> "np.ndarray":
        """任意输入转成一维单声道 float64 数组（整型满幅归一化）。"""
        array = np.asarray(samples).reshape(-1)
        if array.size == 0:
            return np.empty(0, dtype=np.float64)
        if np.issubdtype(array.dtype, np.integer):
            info = np.iinfo(array.dtype)
            span = float(max(abs(info.min), info.max))
            array = array.astype(np.float64) / span
        else:
            array = array.astype(np.float64)
        if channels > 1 and array.size >= channels:
            # 摊平后的交错多声道取声道均值（尾部不足一帧声道的不完整样本丢弃）
            usable = array.size - (array.size % channels)
            array = array[:usable].reshape(-1, channels).mean(axis=1)
        return array


class SentenceBoundarySink:
    """音频分接接收方（``AudioSink`` 会话形状）：播放时顺带检句间隙并发事件。

    一次发言 = 一次 ``start → feed×N → stop``。``feed`` 同步、非阻塞：只算
    检测器，检出后把事件发布调度回事件循环，不在播放线程上做任何慢操作。
    """

    def __init__(
        self,
        event_bus: Optional[EventBus],
        *,
        detector: Optional[SilenceGapDetector] = None,
        logger_name: str = "SentenceBoundarySink",
    ) -> None:
        self._event_bus = event_bus
        self._detector = detector if detector is not None else SilenceGapDetector()
        self.logger = get_logger(logger_name)
        self._utterance_id: str = ""
        self._segment_index = 0
        # feed 可能从播放工作线程被调（流式写块走线程池），事件发布须回到
        # 事件循环线程：start 时（必在循环线程）捕获循环引用
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, utterance_id: str = "") -> None:
        """会话开始（播放器开播时同步调用）：复位检测器并记录 utterance_id。"""
        try:
            self._detector.reset()
            self._utterance_id = utterance_id
            self._segment_index = 0
            self._loop = asyncio.get_running_loop()
        except Exception as exc:  # noqa: BLE001 - 装饰性路径边界
            self.logger.warning(f"句间隙检测会话启动异常（本句不检测）: {exc}")
            self._utterance_id = ""

    def feed(self, chunk: "np.ndarray", sample_rate: int) -> None:
        """递入一个音频块（同步、非阻塞）；检出间隙即调度句边界事件发布。"""
        if not self._utterance_id:
            return
        try:
            if self._detector.feed(chunk, sample_rate):
                self._segment_index += 1
                self._schedule_emit()
        except Exception as exc:  # noqa: BLE001 - 装饰性路径边界
            self.logger.warning(f"句间隙检测异常（已忽略）: utterance_id={self._utterance_id}, err={exc}")

    def stop(self) -> None:
        """会话结束（播放器播完时同步调用）：复位一切会话状态。"""
        self._detector.reset()
        self._utterance_id = ""
        self._segment_index = 0
        self._loop = None

    def _schedule_emit(self) -> None:
        """把事件发布调度回事件循环线程（feed 可能在工作线程）。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        utterance_id = self._utterance_id
        segment_index = self._segment_index
        loop.call_soon_threadsafe(self._spawn_emit, utterance_id, segment_index)

    def _spawn_emit(self, utterance_id: str, segment_index: int) -> None:
        """在事件循环线程创建发布任务（call_soon_threadsafe 的回调面）。"""
        asyncio.get_running_loop().create_task(self._emit(utterance_id, segment_index))

    async def _emit(self, utterance_id: str, segment_index: int) -> None:
        """发布句边界事件；发布失败只记日志（字幕还有兜底定时，不致命）。"""
        if self._event_bus is None:
            return
        try:
            payload = UtteranceSentenceBoundaryPayload(
                utterance_id=utterance_id,
                segment_index=segment_index,
                timestamp_ms=now_ms(),
            )
            await self._event_bus.emit(CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, payload, source="tts")
        except Exception as exc:  # noqa: BLE001 - 装饰性路径边界
            self.logger.warning(f"句边界事件发布异常（已忽略）: utterance_id={utterance_id}, err={exc}")


__all__ = [
    "SilenceGapDetector",
    "SentenceBoundarySink",
    "DEFAULT_MIN_GAP_MS",
    "DEFAULT_FRAME_MS",
    "DEFAULT_SILENCE_THRESHOLD",
]
