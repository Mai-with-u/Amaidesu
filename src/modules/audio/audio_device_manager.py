"""
音频设备管理器

提供音频设备管理功能：
- 设备查询
- 设备选择
- 音频流播放
"""

import asyncio
from dataclasses import dataclass, field
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from src.modules.logging import get_logger

from .pcm_stream_buffer import PcmStreamBuffer, UnderrunStats

# sounddevice 是可选依赖：缺失时保持模块可加载，相关 API 走优雅降级路径。
# 注意 import sounddevice 有副作用——导入期即初始化 PortAudio，无音频子系统
# 的环境（如无头 CI 的 Linux runner）会抛 PortAudioError 而非 ImportError，
# 故降级须一并兜住；否则模块在收集期炸穿，"保持可加载"名存实亡。
DEPENDENCIES_OK = False


class _FallbackCallbackStop(Exception):
    """sounddevice 不可用或被替身替换时的流停止回调占位异常。"""


_CALLBACK_STOP_CLS: type[BaseException] = _FallbackCallbackStop
try:
    import sounddevice as sd

    _CALLBACK_STOP_CLS = getattr(sd, "CallbackStop", _FallbackCallbackStop)
    DEPENDENCIES_OK = True
except ImportError as exc:
    get_logger("AudioDeviceManager").debug(f"sounddevice 未安装，设备枚举与播放 API 走降级路径: {exc}")
except Exception as exc:  # noqa: BLE001 - 降级路径本身：记录后按依赖不可用处理
    get_logger("AudioDeviceManager").debug(
        f"sounddevice 导入期初始化失败，音频 API 走降级路径: {type(exc).__name__}: {exc}"
    )


@dataclass
class _StreamSession:
    """单次流式播放会话的运行期状态。"""

    stream: Any
    buffer: PcmStreamBuffer
    sink_queue: Optional[queue.Queue[Optional[np.ndarray]]]
    sink_drop: threading.Event
    # 序列化同一底层流上的 start 与 stop/abort/close，绝不跨排空等待持有
    stream_op_lock: threading.Lock = field(default_factory=threading.Lock)
    sink_thread: Optional[threading.Thread] = None
    utterance_id: str = ""
    started: bool = False
    closed: bool = False
    underrun_reported_events: int = 0


class AudioDeviceManager:
    """
    音频设备管理器

    管理音频输出设备和音频播放。可挂载一个 ``AudioSink`` 分接（全量与
    流式两种播法都会把音频复制递入；分接是装饰性路径，任何 sink 异常
    只记日志，不影响播放）。流式播放通过 PortAudio 异步回调驱动线程安全
    PCM 队列：上游非阻塞入队，回调按声卡帧率实时消费，队列在句中读空时
    显式填入静音零值（避免 MME 等老驱动循环末端硬件缓冲拖出长音怪叫）；
    分接随回调已播帧经独立后台泵递入，使口型和句间隙检测贴合听感进度。
    """

    def __init__(
        self,
        sample_rate: int = 32000,
        channels: int = 1,
        dtype: type = np.int16,
        sink: Optional[Any] = None,
        prebuffer_seconds: float = 2.5,
        stall_threshold_ms: int = 5000,
        stall_watchdog_enabled: bool = True,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """
        初始化音频设备管理器

        Args:
            sample_rate: 采样率
            channels: 声道数
            dtype: 数据类型
            sink: 可选音频分接接收方（``AudioSink`` 协议形状；口型分析等
                消费者经构造链注入）
            prebuffer_seconds: 流式播放预缓冲秒数——声卡流攒够这么多音频才
                开播，吸收上游合成/网络的短暂停顿；上游吞吐长期低于实时或
                句中停顿超过余量时，回调自动补静音零值防止硬件重复末帧
            stall_threshold_ms: 播放中断流判定阈值（毫秒）——播放会话进行中
                且超过该时长没有收到任何新音频块即告警
            stall_watchdog_enabled: 是否在播放会话期间启动后台巡检线程。
                上游完全无数据时写块函数不会被调用，断流只能靠定时巡检发现；
                测试关闭后改为直接调用检测方法，使时间推进完全可控
            clock: 单调时钟（秒），缺省用 ``time.monotonic``；注入后断流
                判定所依据的时间推进可由测试控制
        """
        self.logger = get_logger("AudioDeviceManager")
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self._sink = sink
        self.prebuffer_seconds = prebuffer_seconds

        # 设备配置
        self.output_device_name: Optional[str] = None
        self.output_device_index: Optional[int] = None

        # 播放状态（由 _state_lock 保护跨线程读写）
        self._state_lock = threading.Lock()
        self.is_playing = False
        self.current_stream: Optional[Any] = None
        self._stream: Optional[Any] = None
        self._stream_started = False
        self._prepared_sessions: Dict[int, _StreamSession] = {}
        self._active_session: Optional[_StreamSession] = None
        self._last_underrun_stats: Optional[UnderrunStats] = None

        # 供给块间诊断与断流巡检：用实时缓冲余量替代旧写阻塞扣除，
        # 数据充裕时不误报断流，真停供耗尽后照常告警
        self._last_feed_monotonic: float | None = None
        self.stall_threshold_ms = stall_threshold_ms
        self.stall_watchdog_enabled = stall_watchdog_enabled
        self._clock: Optional[Callable[[], float]] = clock
        self._last_block_monotonic: float | None = None
        self._stall_alerted: bool = False
        self._playback_session_active: bool = False
        self._playback_begun: bool = False
        self._stall_stop_event = threading.Event()
        self._stall_watchdog: Optional[threading.Thread] = None
        self._stall_poll_interval_s = 0.5
        # 全量播法（play_audio）分接逐片步长：与事件循环异步等待节奏一致
        self._PLAYBACK_FEED_PIECE_S = 0.05

        if not DEPENDENCIES_OK:
            self.logger.error("音频依赖缺失，请安装: pip install sounddevice soundfile")

    @property
    def is_stream_started(self) -> bool:
        """当前流式播放会话的底层声卡流是否已启动"""
        with self._state_lock:
            return self._stream_started

    @property
    def is_stream_draining(self) -> bool:
        """当前流式播放会话是否已收到上游结束信号、处于排空收尾期"""
        with self._state_lock:
            session = self._active_session
        return session is not None and session.buffer.is_eos

    @property
    def buffered_frames(self) -> int:
        """当前流式播放缓冲队列中的待播帧数"""
        with self._state_lock:
            session = self._active_session
        return session.buffer.buffered_frames if session is not None else 0

    @property
    def stream_underrun_stats(self) -> Optional[UnderrunStats]:
        """返回活动会话的实时欠载统计；无活动会话时返回上一句的收尾统计"""
        with self._state_lock:
            session = self._active_session
            last_stats = self._last_underrun_stats
        if session is not None:
            return session.buffer.stats()
        return last_stats

    def wait_until_sink_drained(self) -> None:
        """等待当前会话已递给分接泵的音频帧全部消费完毕"""
        with self._state_lock:
            session = self._active_session
            sink_queue = session.sink_queue if session is not None else None
        if sink_queue is not None:
            sink_queue.join()

    # -----分接（复制音频给注入的 sink；装饰性路径 fail-soft）-----

    def _sink_start(self, utterance_id: str = "") -> None:
        if self._sink is None:
            return
        try:
            self._sink.start(utterance_id)
        except Exception as e:
            self.logger.warning(f"音频分接 start 异常（已忽略）: {e}")

    def _sink_feed(self, chunk: "np.ndarray", sample_rate: int) -> None:
        if self._sink is None:
            return
        try:
            self._sink.feed(chunk, sample_rate)
        except Exception as e:
            self.logger.warning(f"音频分接 feed 异常（已忽略）: {e}")

    def _sink_stop(self) -> None:
        if self._sink is None:
            return
        try:
            self._sink.stop()
        except Exception as e:
            self.logger.warning(f"音频分接 stop 异常（已忽略）: {e}")

    def find_device_index(self, device_name: Optional[str]) -> Optional[int]:
        """
        根据设备名称查找设备索引

        Args:
            device_name: 设备名称

        Returns:
            设备索引，如果未找到则返回 None
        """
        if not DEPENDENCIES_OK:
            self.logger.error("sounddevice 库不可用")
            return None

        try:
            devices = sd.query_devices()
            if device_name:
                for i, device in enumerate(devices):
                    device_name_attr = getattr(device, "name", "")
                    max_output_channels = getattr(device, "max_output_channels", 0)
                    if device_name.lower() in device_name_attr.lower() and max_output_channels > 0:
                        self.logger.info(f"找到输出设备: {device_name_attr} (索引: {i})")
                        return i
                self.logger.warning(f"未找到名称包含 '{device_name}' 的输出设备，使用默认设备")

            # 使用默认输出设备
            default_device_indices = sd.default.device
            default_index = default_device_indices[1] if default_device_indices[1] != -1 else None
            if default_index is not None:
                default_device_name_attr = getattr(devices[default_index], "name", "Unknown")
                self.logger.info(f"使用默认输出设备: {default_device_name_attr} (索引: {default_index})")
                return default_index

            self.logger.warning("未找到默认输出设备，将由 sounddevice 选择")
            return None
        except Exception as e:
            self.logger.error(f"查找音频设备时出错: {e}")
            return None

    def set_output_device(self, device_name: Optional[str] = None, device_index: Optional[int] = None) -> None:
        """
        设置输出设备

        Args:
            device_name: 设备名称
            device_index: 设备索引
        """
        if device_index is not None:
            self.output_device_index = device_index
            self.output_device_name = None
            self.logger.info(f"设置输出设备索引: {device_index}")
        elif device_name:
            self.output_device_name = device_name
            self.output_device_index = self.find_device_index(device_name)
            self.logger.info(f"设置输出设备名称: {device_name}")

    def list_output_devices(self) -> List[Dict[str, Any]]:
        """
        列出所有输出设备

        Returns:
            设备信息列表
        """
        if not DEPENDENCIES_OK:
            self.logger.error("sounddevice 库不可用")
            return []

        try:
            devices = sd.query_devices()
            output_devices = []

            for i, device in enumerate(devices):
                max_output_channels = getattr(device, "max_output_channels", 0)
                if max_output_channels > 0:
                    output_devices.append(
                        {
                            "index": i,
                            "name": getattr(device, "name", "Unknown"),
                            "max_output_channels": max_output_channels,
                            "default_samplerate": getattr(device, "default_samplerate", 0),
                        }
                    )

            return output_devices
        except Exception as e:
            self.logger.error(f"列出输出设备时出错: {e}")
            return []

    async def play_audio(
        self,
        audio_array: np.ndarray,
        samplerate: Optional[int] = None,
        device_index: Optional[int] = None,
        utterance_id: str = "",
    ) -> None:
        """
        播放音频

        Args:
            audio_array: 音频数据数组
            samplerate: 采样率（默认使用 self.sample_rate）
            device_index: 设备索引（默认使用 self.output_device_index）
            utterance_id: 本次发声实例 ID，随分接会话递给 sink
                （流式播法的 start_prepared_stream 同样递入；缺省空串）
        """
        if not DEPENDENCIES_OK:
            self.logger.error("音频依赖缺失")
            raise RuntimeError("音频依赖缺失")

        if samplerate is None:
            samplerate = self.sample_rate
        if device_index is None:
            device_index = self.output_device_index

        self.logger.debug(f"开始播放音频 (设备索引: {device_index}, 采样率: {samplerate})...")

        # 分接：开播同步开 session（全量播法按播放节奏逐片递入）
        self._sink_start(utterance_id)

        try:
            # 停止现有播放
            sd.stop()

            # 播放音频
            sd.play(audio_array, samplerate=samplerate, device=device_index)
            self.is_playing = True

            # 分接按播放节奏逐片递入：整段一次递入会让分接缓冲直接跳到
            # 句尾，句身整段被消费方跳过（口型不动的根因）
            piece_samples = max(1, int(self._PLAYBACK_FEED_PIECE_S * samplerate))
            for start in range(0, len(audio_array), piece_samples):
                self._sink_feed(audio_array[start : start + piece_samples], samplerate)
                await asyncio.sleep(piece_samples / samplerate)
            await asyncio.sleep(0.3)  # 收尾余量（原 wait_time 的 0.3 秒尾）

            # 确保播放停止
            sd.stop()
            self.is_playing = False
            # 分接：播完同步收 session
            self._sink_stop()

            self.logger.debug("音频播放完成")

        except asyncio.CancelledError:
            # 取消即刻截断在播全量音频并收口分接会话，防止继续出声或口型残留
            try:
                sd.stop()
            except Exception as exc:  # noqa: BLE001 - 取消收尾仅记日志，继续状态复位并重抛 CancelledError
                self.logger.debug(f"全量播放取消时停止声卡异常（已忽略）: {exc}")
            self.is_playing = False
            self._sink_stop()
            raise
        except Exception as e:
            self.logger.exception(f"音频播放失败: {e}")
            self.is_playing = False
            self._sink_stop()
            raise

    def open_output_stream(self) -> Optional[Any]:
        """打开一个声卡异步回调输出流（不开播、不碰分接）；打不开时记日志返回 None。

        打开声卡在 Windows 上要几十到上百毫秒，在事件循环线程里调用会让同一
        循环上的一切顿一下（形象每句开口都卡一帧），事件循环里的调用方应放到
        工作线程，再用 :meth:`start_prepared_stream` 开始这一句。
        """
        if not DEPENDENCIES_OK:
            self.logger.error("sounddevice 库不可用")
            return None
        try:
            sink_queue: Optional[queue.Queue[Optional[np.ndarray]]] = queue.Queue() if self._sink is not None else None
            sink_drop = threading.Event()
            buffer = PcmStreamBuffer(
                sample_rate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                on_played_frames=sink_queue.put_nowait if sink_queue is not None else None,
            )

            # 实时声卡回调只从环形缓冲拷贝 PCM 与缺口补零：不在回调线程打日志或同步喂分接
            def _audio_callback(outdata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
                _ = (frames, time_info)
                if status:
                    buffer.record_driver_status(str(status))
                if buffer.pull_into(outdata):
                    raise _CALLBACK_STOP_CLS

            stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                device=self.output_device_index,
                callback=_audio_callback,
                finished_callback=buffer.mark_stream_finished,
            )
            session = _StreamSession(
                stream=stream,
                buffer=buffer,
                sink_queue=sink_queue,
                sink_drop=sink_drop,
            )
            with self._state_lock:
                self._prepared_sessions[id(stream)] = session
            return stream
        except Exception as e:
            self.logger.error(f"打开声卡输出流失败: {e}")
            return None

    def start_stream(self, utterance_id: str = "") -> None:
        """启动流式播放：创建回调声卡流但暂不开播，等预缓冲攒够再启动（同步打开声卡，见 open_output_stream）。

        上游块非阻塞入队；攒满 ``prebuffer_seconds`` 后由 ``write_chunk``
        启动声卡流。短句（总量不足预缓冲）在 ``stop_stream`` 收尾开播排空。
        """
        self.start_prepared_stream(self.open_output_stream(), utterance_id)

    def _run_sink_pump(
        self,
        sink_queue: "queue.Queue[Optional[np.ndarray]]",
        drop_flag: threading.Event,
    ) -> None:
        """后台消费已播帧队列并递入 AudioSink：让口型与句间隙检测脱离音频回调线程。"""
        while True:
            item = sink_queue.get()
            try:
                if item is None:
                    return
                if drop_flag.is_set():
                    continue
                self._sink_feed(item, self.sample_rate)
            finally:
                sink_queue.task_done()

    def _close_stream_handle(self, stream: Any, *, use_abort: bool) -> None:
        """安全停止并关闭底层声卡流句柄。"""
        try:
            if use_abort and hasattr(stream, "abort"):
                stream.abort()
            else:
                stream.stop()
        except Exception as e:
            self.logger.warning(f"停止音频流失败: {e}")
        try:
            stream.close()
        except Exception as e:
            self.logger.warning(f"关闭音频流失败: {e}")

    def start_prepared_stream(self, stream: Optional[Any], utterance_id: str = "") -> None:
        """用已打开的声卡流开始一句（``stream`` 为 None 时这一句不出声）。

        分接会话在这里同步开启：口型分析器要在事件循环里起后台任务，所以本方法
        要在事件循环线程调用（慢的打开声卡已经在 open_output_stream 里做完）。
        """
        if stream is None:
            # 声卡没打开：清理分接，不把失败路径当作成功播放
            with self._state_lock:
                self._stream = None
                self._stream_started = False
                self.is_playing = False
            self._sink_stop()
            return

        # 兜底清理上次残留未关的活动会话，防止句柄泄漏
        with self._state_lock:
            has_stale_active = self._active_session is not None
        if has_stale_active:
            self.stop_stream(discard_pending=True)

        with self._state_lock:
            session = self._prepared_sessions.pop(id(stream), None)
        if session is None:
            # 外部直接传入裸流时兜底构建一个未关联回调的会话对象，保证生命周期字段完整
            sink_queue: Optional[queue.Queue[Optional[np.ndarray]]] = queue.Queue() if self._sink is not None else None
            sink_drop = threading.Event()
            buffer = PcmStreamBuffer(
                sample_rate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                on_played_frames=sink_queue.put_nowait if sink_queue is not None else None,
            )
            session = _StreamSession(
                stream=stream,
                buffer=buffer,
                sink_queue=sink_queue,
                sink_drop=sink_drop,
            )

        session.utterance_id = utterance_id
        try:
            with self._state_lock:
                self._stream = stream
                self._active_session = session
                self._stream_started = False
                self._last_feed_monotonic = None
                self.is_playing = True
            # 分接必须在事件循环线程开启，且早于后台分接泵启动，确保 start 先于任意 feed
            self._sink_start(utterance_id)
            if session.sink_queue is not None:
                sink_thread = threading.Thread(
                    target=self._run_sink_pump,
                    args=(session.sink_queue, session.sink_drop),
                    name="audio-sink-pump",
                    daemon=True,
                )
                session.sink_thread = sink_thread
                sink_thread.start()
            self._start_playback_session()
            self.logger.debug("流式回调播放已就绪（等待预缓冲）")
        except Exception as e:
            self.logger.error(f"启动流式播放失败: {e}")
            with self._state_lock:
                if self._active_session is session:
                    self._active_session = None
                if self._stream is stream:
                    self._stream = None
                self._stream_started = False
                self.is_playing = False
            session.sink_drop.set()
            session.buffer.abort()
            with session.stream_op_lock:
                if not session.closed:
                    self._close_stream_handle(session.stream, use_abort=True)
                    session.closed = True
            self._finish_sink_pump(session)
            self._sink_stop()
            self._stop_playback_session()

    def _finish_sink_pump(self, session: _StreamSession) -> None:
        """优雅结束分接泵线程（推入哨兵并等待已入队帧处理完成）。"""
        if session.sink_queue is None:
            return
        session.sink_queue.put_nowait(None)
        sink_thread = session.sink_thread
        if sink_thread is not None and sink_thread is not threading.current_thread():
            sink_thread.join(timeout=2.0)
            if sink_thread.is_alive():
                self.logger.warning(f"音频分接泵收尾超时未退出: utterance_id={session.utterance_id or '-'}")

    # -----播放中断流与实时欠载检测（播放会话期间的上游供给监控）-----

    def _now(self) -> float:
        """当前单调时刻（秒）。注入时钟优先，缺省取系统单调时钟。"""
        if self._clock is not None:
            return self._clock()
        return time.monotonic()

    def _record_block_arrival(self) -> None:
        """记录一个上游音频块的到达时刻（断流判定的基线）。"""
        with self._state_lock:
            self._last_block_monotonic = self._now()
            self._stall_alerted = False

    def _start_playback_session(self) -> None:
        """开启播放会话监控：会话期间声卡开播后上游应有块持续到达。

        告警只在声卡流实际开播后判定（见 :meth:`check_playback_stall`）；
        开播前的合成慢/预缓冲期属于发言间静默，不进告警面。
        """
        with self._state_lock:
            self._playback_session_active = True
            self._playback_begun = False
            self._stall_alerted = False
            self._last_block_monotonic = self._now()
        if not self.stall_watchdog_enabled:
            return
        self._stall_stop_event.clear()
        self._stall_watchdog = threading.Thread(target=self._watchdog_loop, name="audio-stall-watchdog", daemon=True)
        self._stall_watchdog.start()

    def _stop_playback_session(self) -> None:
        """关闭播放会话监控（句间静默期不判断流）。"""
        with self._state_lock:
            self._playback_session_active = False
            self._playback_begun = False
        self._stall_stop_event.set()
        watchdog = self._stall_watchdog
        self._stall_watchdog = None
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=1.0)

    def _watchdog_loop(self) -> None:
        """后台巡检：定时判定播放会话是否断流或已发生真实声卡欠载。"""
        while not self._stall_stop_event.wait(self._stall_poll_interval_s):
            try:
                self.check_playback_stall()
                self.check_playback_underrun()
            except Exception as e:  # noqa: BLE001 - 巡检线程不得因单次异常退出
                self.logger.error(f"播放监控巡检异常: {e}")

    def check_playback_stall(self) -> bool:
        """检测播放中断流：声卡流已开播、尚未 EOS 且缓冲余量扣除后仍超阈值无新块即告警。

        Returns:
            本次调用是否发出了告警。
        """
        with self._state_lock:
            session = self._active_session
            if (
                not self._playback_session_active
                or not self._playback_begun
                or self._last_block_monotonic is None
                or session is None
            ):
                return False
            last_block = self._last_block_monotonic
            already_alerted = self._stall_alerted

        if session.buffer.is_eos or session.buffer.is_aborted:
            return False

        # 扣除缓冲队列里尚未消耗的可播余量：上游突发推完一大段后短暂暂停不算断流，缓冲耗尽后才算真停供
        idle_sec = (self._now() - last_block) - session.buffer.buffered_seconds()
        if idle_sec * 1000 < self.stall_threshold_ms:
            return False
        if already_alerted:
            return False

        with self._state_lock:
            if self._active_session is not session or self._stall_alerted:
                return False
            self._stall_alerted = True

        self.logger.warning(f"播放中断流：正在播放但已 {idle_sec:.1f}s 未收到新音频块（TTS 上游供给中断）")
        return True

    def check_playback_underrun(self) -> bool:
        """检查当前活动流是否新增了真实欠载（回调在句中读空缓冲并补零静音）。

        Returns:
            本次调用是否发出了新的欠载告警。
        """
        with self._state_lock:
            session = self._active_session
            if session is None or not session.started or session.buffer.is_aborted:
                return False
            stats = session.buffer.stats()
            if stats.events <= session.underrun_reported_events:
                return False
            session.underrun_reported_events = stats.events
            utterance_id = session.utterance_id

        self.logger.warning(
            f"声卡播放欠载：utterance_id={utterance_id or '-'}，"
            f"累计 {stats.events} 次（已补静音 {stats.duration_ms}ms，等待上游补块）"
        )
        return True

    def _start_session_stream_if_needed(self, session: _StreamSession) -> None:
        """在流级操作锁保护下启动尚未开播且未关闭/未中止的声卡流。"""
        started_now = False
        with session.stream_op_lock:
            if not session.started and not session.closed and not session.buffer.is_aborted:
                session.stream.start()
                session.started = True
                started_now = True
        if started_now:
            with self._state_lock:
                if self._active_session is session:
                    self._stream_started = True
                    self._playback_begun = True

    def write_chunk(self, chunk: np.ndarray) -> None:
        """向流式播放缓冲非阻塞写入一个音频块。

        预缓冲未满时仅入队暂不开播；攒满 ``prebuffer_seconds`` 后启动声卡回调流，
        队列继续作为实时缓冲吸收后续块间抖动，函数立即返回不阻塞等播放。

        Args:
            chunk: 音频数据块 (1D 或 2D ndarray)
        """
        if not DEPENDENCIES_OK:
            return
        with self._state_lock:
            session = self._active_session
            last_feed = self._last_feed_monotonic
        if session is None:
            return

        try:
            now = self._now()
            if last_feed is not None:
                gap = now - last_feed
                if gap > 2.0:
                    # 块间隔偏大只作诊断信息：回调由缓冲余量兜底，缓冲耗尽由欠载/断流监控上报
                    self.logger.debug(
                        f"上游块间隔偏大 {gap:.1f}s（当前缓冲余量 {session.buffer.buffered_seconds():.2f}s）"
                    )
            with self._state_lock:
                if self._active_session is session:
                    self._last_feed_monotonic = now
                    self._last_block_monotonic = now
                    self._stall_alerted = False

            session.buffer.push(chunk)

            if not session.started and session.buffer.ready_to_start(self.prebuffer_seconds):
                self._start_session_stream_if_needed(session)
                if session.started:
                    self.logger.debug(
                        f"预缓冲达阈值（{self.prebuffer_seconds:.1f}s），声卡回调流开播"
                        f"（当前缓冲 {session.buffer.buffered_seconds():.2f}s）"
                    )

            self.check_playback_underrun()
        except Exception as e:
            self.logger.error(f"写入音频流失败: {e}")

    def _teardown_session(self, session: _StreamSession, *, abort_stream: bool) -> None:
        """收尾单次流式播放会话：关闭底层流、排空分接泵、结束监控并汇报统计。"""
        with session.stream_op_lock:
            if not session.closed:
                self._close_stream_handle(session.stream, use_abort=abort_stream)
                session.closed = True

        self._finish_sink_pump(session)
        self._sink_stop()
        self._stop_playback_session()

        stats = session.buffer.stats()
        driver_statuses = session.buffer.driver_statuses()
        with self._state_lock:
            self._last_underrun_stats = stats

        if stats.events > 0 or driver_statuses:
            status_suffix = f", driver_statuses={driver_statuses}" if driver_statuses else ""
            self.logger.warning(
                f"流式播放结束含欠载/驱动告警：utterance_id={session.utterance_id or '-'}, "
                f"events={stats.events}, 补零静音={stats.duration_ms}ms, "
                f"最长单次={stats.max_contiguous_ms}ms, 已播={stats.played_ms}ms{status_suffix}"
            )
        self.logger.debug(f"流式播放已停止（已播 {stats.played_ms}ms）")

    def stop_stream(self, discard_pending: bool = False) -> None:
        """停止并关闭流式播放。

        Args:
            discard_pending: True 时立即中止声卡流并丢弃所有未播缓冲（取消/截断语义，
                可瞬间打断另一线程正在进行的排空等待）；默认 False 时标记上游结束，
                等待余量音频按回调正常播完再收尾。
        """
        if not DEPENDENCIES_OK:
            return

        aborted_prepared: list[_StreamSession] = []
        owns_teardown = False
        session: Optional[_StreamSession] = None

        with self._state_lock:
            if discard_pending and self._prepared_sessions:
                aborted_prepared = list(self._prepared_sessions.values())
                self._prepared_sessions.clear()
            session = self._active_session
            if session is not None and discard_pending:
                # 取消路径立刻抢占清理权并清状态，同时置 abort 唤醒可能阻塞在 wait_finished 的正常收尾线程
                self._active_session = None
                self._stream = None
                self._stream_started = False
                self.is_playing = False
                self._last_feed_monotonic = None
                self._playback_session_active = False
                self._playback_begun = False
                owns_teardown = True

        if session is None:
            for prep in aborted_prepared:
                with prep.stream_op_lock:
                    if not prep.closed:
                        self._close_stream_handle(prep.stream, use_abort=True)
                        prep.closed = True
            return

        if discard_pending:
            session.sink_drop.set()
            session.buffer.abort()
        else:
            has_audio = session.buffer.mark_eos()
            if has_audio and not session.started and not session.buffer.is_aborted:
                try:
                    self._start_session_stream_if_needed(session)
                except Exception as e:
                    self.logger.warning(f"收尾启动短句声卡流失败: {e}")

            if session.started and not session.buffer.is_aborted:
                drain_timeout_s = max(5.0, session.buffer.buffered_seconds() + 5.0)
                finished = session.buffer.wait_finished(timeout=drain_timeout_s)
                if not finished and not session.buffer.is_aborted:
                    self.logger.warning(f"流式播放排空超时（>{drain_timeout_s:.1f}s），强制中止底层音频流")
                    session.buffer.abort()

            with self._state_lock:
                # 若并发取消已抢先接管清理，当前线程直接退出，避免双关流与双停分接
                if self._active_session is session:
                    self._active_session = None
                    self._stream = None
                    self._stream_started = False
                    self.is_playing = False
                    self._last_feed_monotonic = None
                    self._playback_session_active = False
                    self._playback_begun = False
                    owns_teardown = True

        if owns_teardown:
            self._teardown_session(session, abort_stream=discard_pending or session.buffer.is_aborted)

        for prep in aborted_prepared:
            with prep.stream_op_lock:
                if not prep.closed:
                    self._close_stream_handle(prep.stream, use_abort=True)
                    prep.closed = True

    def stop_audio(self) -> None:
        """停止音频播放"""
        if not DEPENDENCIES_OK:
            return

        try:
            sd.stop()
            self.is_playing = False
            self.logger.debug("音频播放已停止")
        except Exception as e:
            self.logger.warning(f"停止音频播放失败: {e}")

    def get_device_info(self, device_index: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        获取设备信息

        Args:
            device_index: 设备索引（默认使用当前设备）

        Returns:
            设备信息字典
        """
        if not DEPENDENCIES_OK:
            return None

        try:
            if device_index is None:
                device_index = self.output_device_index

            if device_index is None:
                # 使用默认设备
                default_device_indices = sd.default.device
                device_index = default_device_indices[1] if default_device_indices[1] != -1 else None

            if device_index is None:
                return None

            device = sd.query_devices(device_index)
            return {
                "index": device_index,
                "name": getattr(device, "name", "Unknown"),
                "max_output_channels": getattr(device, "max_output_channels", 0),
                "max_input_channels": getattr(device, "max_input_channels", 0),
                "default_samplerate": getattr(device, "default_samplerate", 0),
            }
        except Exception as e:
            self.logger.error(f"获取设备信息失败: {e}")
            return None
