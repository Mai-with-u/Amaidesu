"""流式音频环形队列与实时欠载监测

在音频生产者（TTS 流式拉流写入）与消费者（PortAudio 实时输出回调）之间
建立线程安全的无阻塞缓冲区：

- 写入侧立即入队返回，不阻塞等声卡消耗；
- 回调侧按声卡帧率实时取样；队列在句中读空时显式填入静音零值（0），
  避免 Windows MME 等老驱动循环重复末端硬件缓冲而拖出长音怪叫；
- 同时统计真实欠载事件次数、补零静音帧数与驱动回调状态标记，并把实际
  播出的帧经非阻塞钩子转发给分接泵，使口型和句间隙检测始终贴合播放游标。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
from typing import Any, Callable, Optional

import numpy as np

# 驱动回调状态标记上限：只保留少量样本供停流告警定位，防止持续异常写满内存
_MAX_DRIVER_STATUS_RECORDS = 16


@dataclass(frozen=True)
class UnderrunStats:
    """单次流式播放会话的欠载与播放统计快照"""

    events: int = 0
    frames: int = 0
    max_contiguous_frames: int = 0
    played_frames: int = 0
    sample_rate: int = 0

    @property
    def duration_ms(self) -> int:
        """累计欠载补零静音时长（毫秒）"""
        if self.sample_rate <= 0 or self.frames <= 0:
            return 0
        return int(round(self.frames * 1000 / self.sample_rate))

    @property
    def max_contiguous_ms(self) -> int:
        """最长单次连续欠载静音时长（毫秒）"""
        if self.sample_rate <= 0 or self.max_contiguous_frames <= 0:
            return 0
        return int(round(self.max_contiguous_frames * 1000 / self.sample_rate))

    @property
    def played_ms(self) -> int:
        """累计从缓冲实际取出的音频时长（毫秒，不含欠载补零与句尾对齐）"""
        if self.sample_rate <= 0 or self.played_frames <= 0:
            return 0
        return int(round(self.played_frames * 1000 / self.sample_rate))


class PcmStreamBuffer:
    """线程安全的流式 PCM 缓冲队列（实时回调读出 + 欠载显式补零）"""

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int = 1,
        dtype: Any = np.int16,
        on_played_frames: Optional[Callable[[np.ndarray], None]] = None,
    ) -> None:
        self._sample_rate = max(1, int(sample_rate))
        self._channels = max(1, int(channels))
        self._dtype = np.dtype(dtype)
        self._on_played_frames = on_played_frames

        self._lock = threading.Lock()
        self._queue: deque[np.ndarray] = deque()
        self._head_offset = 0
        self._buffered_frames = 0
        self._eos = False
        self._aborted = False
        self._stream_finished = threading.Event()

        # 播放与欠载计数：played_frames 只记真实 PCM 样本，补零静默单独记入 underrun
        self._played_frames = 0
        self._underrun_events = 0
        self._underrun_frames = 0
        self._current_underrun_frames = 0
        self._max_contiguous_underrun_frames = 0
        self._in_underrun = False
        self._driver_statuses: list[str] = []

    def _normalize_chunk(self, chunk: np.ndarray) -> np.ndarray:
        """把上游传入的任意维音频块规范化为独立的 ``(frames, channels)`` 连续数组。"""
        arr = np.asarray(chunk)
        if arr.size == 0:
            return np.empty((0, self._channels), dtype=self._dtype)

        if arr.ndim == 2:
            if arr.shape[1] == self._channels:
                shaped = arr
            elif arr.shape[1] > self._channels:
                shaped = arr[:, : self._channels]
            elif arr.shape[1] == 1 and self._channels > 1:
                # 单声道二维输入扩展到多声道：保持帧数不变
                shaped = np.repeat(arr, self._channels, axis=1)
            else:
                flat = arr.reshape(-1)
                usable = flat.size - (flat.size % self._channels)
                if usable == 0:
                    return np.empty((0, self._channels), dtype=self._dtype)
                shaped = flat[:usable].reshape(-1, self._channels)
        else:
            flat = arr.reshape(-1)
            if self._channels == 1:
                shaped = flat.reshape(-1, 1)
            else:
                usable = flat.size - (flat.size % self._channels)
                if usable == 0:
                    return np.empty((0, self._channels), dtype=self._dtype)
                shaped = flat[:usable].reshape(-1, self._channels)

        # 显式拷贝，防止调用方后续原地复用同一数组内存污染队列中的数据
        return np.array(shaped, dtype=self._dtype, order="C", copy=True)

    def _format_for_sink(self, view: np.ndarray) -> np.ndarray:
        """把回调刚填充的输出切片格式化成与分接消费方约定一致的独立副本。"""
        if self._channels == 1:
            return np.array(view[:, 0], dtype=self._dtype, order="C", copy=True)
        return np.array(view, dtype=self._dtype, order="C", copy=True)

    @property
    def buffered_frames(self) -> int:
        """当前队列中尚未被回调消费的帧数"""
        with self._lock:
            return self._buffered_frames

    @property
    def is_eos(self) -> bool:
        """上游是否已声明结束写入"""
        with self._lock:
            return self._eos

    @property
    def is_aborted(self) -> bool:
        """会话是否已被中止"""
        with self._lock:
            return self._aborted

    def buffered_seconds(self) -> float:
        """当前队列中缓冲音频的可播时长（秒）"""
        with self._lock:
            return self._buffered_frames / self._sample_rate

    def push(self, chunk: np.ndarray) -> int:
        """将一块音频非阻塞入队，返回入队后总缓冲帧数。

        已中止或已结束（EOS）后再推入的迟到块直接忽略。
        """
        normalized = self._normalize_chunk(chunk)
        with self._lock:
            if self._aborted or self._eos:
                return 0
            if normalized.shape[0] > 0:
                self._queue.append(normalized)
                self._buffered_frames += int(normalized.shape[0])
            return self._buffered_frames

    def ready_to_start(self, prebuffer_seconds: float) -> bool:
        """判断是否已满足启动声卡回调流的条件"""
        with self._lock:
            if self._aborted or self._buffered_frames == 0:
                return False
            if self._eos:
                return True
            threshold_frames = max(0, int(round(max(0.0, prebuffer_seconds) * self._sample_rate)))
            return self._buffered_frames >= threshold_frames

    def mark_eos(self) -> bool:
        """标记上游已停止写入（句尾到达），返回当前是否仍有音频待播"""
        with self._lock:
            self._eos = True
            return self._buffered_frames > 0

    def abort(self) -> None:
        """立即中止播放：丢弃所有未播缓冲并唤醒排空等待"""
        with self._lock:
            self._aborted = True
            self._queue.clear()
            self._head_offset = 0
            self._buffered_frames = 0
        self._stream_finished.set()

    def mark_stream_finished(self) -> None:
        """标记底层音频流已完成排空/停止（挂给 PortAudio ``finished_callback``）"""
        self._stream_finished.set()

    def wait_finished(self, timeout: Optional[float] = None) -> bool:
        """阻塞等待音频流排空完成或被中止"""
        return self._stream_finished.wait(timeout=timeout)

    def record_driver_status(self, status_text: str) -> None:
        """记录底层音频回调携带的非零状态（不在实时回调线程内打日志）"""
        text = status_text.strip()
        if not text:
            return
        with self._lock:
            if len(self._driver_statuses) < _MAX_DRIVER_STATUS_RECORDS:
                self._driver_statuses.append(text)

    def driver_statuses(self) -> list[str]:
        """返回本句记录到的底层音频驱动状态文本列表"""
        with self._lock:
            return list(self._driver_statuses)

    def stats(self) -> UnderrunStats:
        """获取当前已播放与欠载统计快照"""
        with self._lock:
            return UnderrunStats(
                events=self._underrun_events,
                frames=self._underrun_frames,
                max_contiguous_frames=self._max_contiguous_underrun_frames,
                played_frames=self._played_frames,
                sample_rate=self._sample_rate,
            )

    def pull_into(self, outdata: np.ndarray) -> bool:
        """供声卡实时回调拉取一帧区数据；返回 ``True`` 表示流应立即停止。

        队列余量不足请求的 ``frames`` 时，剩余样本一律显式填 0：
        - 若上游仍在生成（未到 EOS）：记一次真实欠载并继续保持流存活，后续块到达即无缝接续；
        - 若上游已到 EOS 且队列抽干：视为正常句尾，填 0 对齐末块并通知流停止。
        """
        out = outdata.reshape(-1, self._channels)
        frames = int(out.shape[0])
        if frames == 0:
            with self._lock:
                return self._aborted or (self._eos and self._buffered_frames == 0)

        should_stop = False
        sink_payload: Optional[np.ndarray] = None

        with self._lock:
            if self._aborted:
                out.fill(0)
                return True

            copied = 0
            while copied < frames and self._queue:
                head = self._queue[0]
                avail = int(head.shape[0]) - self._head_offset
                take = min(avail, frames - copied)
                out[copied : copied + take] = head[self._head_offset : self._head_offset + take]
                copied += take
                self._head_offset += take
                self._buffered_frames -= take
                self._played_frames += take
                if self._head_offset >= head.shape[0]:
                    self._queue.popleft()
                    self._head_offset = 0

            missing = frames - copied
            if missing > 0:
                out[copied:] = 0

            if self._eos and self._buffered_frames == 0:
                # 正常句尾排空：句尾回调对齐补零不送入分接，保持口型/字幕分接样本总数与原音频 1:1 守恒
                self._in_underrun = False
                self._current_underrun_frames = 0
                should_stop = True
                if copied > 0 and self._on_played_frames is not None:
                    sink_payload = self._format_for_sink(out[:copied])
            elif missing > 0:
                # 上游未结束却抽空缓冲：真实句中欠载，显式输出静音并把整段真实输出（含静音）递分接使口型闭嘴
                if not self._in_underrun:
                    self._in_underrun = True
                    self._underrun_events += 1
                    self._current_underrun_frames = 0
                self._underrun_frames += missing
                self._current_underrun_frames += missing
                if self._current_underrun_frames > self._max_contiguous_underrun_frames:
                    self._max_contiguous_underrun_frames = self._current_underrun_frames
                if self._on_played_frames is not None:
                    sink_payload = self._format_for_sink(out)
            else:
                self._in_underrun = False
                self._current_underrun_frames = 0
                if self._on_played_frames is not None:
                    sink_payload = self._format_for_sink(out)

        # 锁外入队，杜绝在音频线程持锁调用外部钩子
        if sink_payload is not None and self._on_played_frames is not None:
            self._on_played_frames(sink_payload)

        return should_stop


__all__ = ["PcmStreamBuffer", "UnderrunStats"]
