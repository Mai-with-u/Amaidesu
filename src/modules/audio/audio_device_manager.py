"""
音频设备管理器

提供音频设备管理功能：
- 设备查询
- 设备选择
- 音频流播放
"""

import asyncio
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from src.modules.logging import get_logger

# sounddevice 是可选依赖：缺失时保持模块可加载，相关 API 走优雅降级路径。
# 注意 import sounddevice 有副作用——导入期即初始化 PortAudio，无音频子系统
# 的环境（如无头 CI 的 Linux runner）会抛 PortAudioError 而非 ImportError，
# 故降级须一并兜住；否则模块在收集期炸穿，"保持可加载"名存实亡。
DEPENDENCIES_OK = False
try:
    import sounddevice as sd

    DEPENDENCIES_OK = True
except ImportError as exc:
    get_logger("AudioDeviceManager").debug(f"sounddevice 未安装，设备枚举与播放 API 走降级路径: {exc}")
except Exception as exc:  # noqa: BLE001 - 降级路径本身：记录后按依赖不可用处理
    get_logger("AudioDeviceManager").debug(
        f"sounddevice 导入期初始化失败，音频 API 走降级路径: {type(exc).__name__}: {exc}"
    )


class AudioDeviceManager:
    """
    音频设备管理器

    管理音频输出设备和音频播放。可挂载一个 ``AudioSink`` 分接（全量与
    流式两种播法都会把音频复制递入；分接是装饰性路径，任何 sink 异常
    只记日志，不影响播放）。分接按**播放进度**递入而非按到达递入——
    到达节奏受合成停顿/整段突发影响，与听感脱节，消费方（口型分析）
    的"分析当前播放位置"假设依赖两者一致。
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
                开播，吸收上游合成/网络的短暂停顿；上游吞吐长期低于实时时
                仍会欠载（那需要上游提速，缓冲无法根治）
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

        # 播放状态（_stream 由 start_stream 创建；未启动时 write/stop 静默跳过）
        self.is_playing = False
        self.current_stream: Optional[Any] = None
        self._stream: Optional[Any] = None
        # 预缓冲状态：攒够 prebuffer_seconds 才启动声卡流
        self._pending_chunks: list[np.ndarray] = []
        self._pending_samples = 0
        self._stream_started = False
        # 供给断流监控：块间间隔扣除写背压（write 等播放消费的阻塞）后仍超
        # 阈值，才判定上游真断供（欠载前兆）——不扣除会把"数据过剩的背压"
        # 误报成断流（turbo 吞吐大于实时时背压为常态，实测每场 80+ 次假阳性）
        self._last_feed_monotonic: float | None = None
        self._last_write_block_sec: float = 0.0
        # 播放中断流检测：utterance 播放会话进行中，若超过阈值没有任何新音频
        # 块到达，判定上游供给已断（告警面）。该检测锚定"正在播放"而非
        # "连接存活"——句间静默期没有播放会话，不告警。检测由独立看门狗线程
        # 驱动（上游完全无数据时写块函数不会被调用，只能靠定时巡检）
        self.stall_threshold_ms = stall_threshold_ms
        self.stall_watchdog_enabled = stall_watchdog_enabled
        self._clock: Optional[Callable[[], float]] = clock
        # 断流判定基线：最近一块的到达时刻（会话开始时初始化为会话起点，
        # 于是"整句一个块都没来"同样落在判定范围内）
        self._last_block_monotonic: float | None = None
        # 累计写声卡阻塞时长与其在"上一块到达时刻"的快照：判定断流时须扣除
        # 这段背压（数据过剩造成的等待，不是上游断供）
        self._backpressure_total: float = 0.0
        self._backpressure_at_last_block: float = 0.0
        self._stall_alerted: bool = False
        self._playback_session_active: bool = False
        self._stall_stop_event = threading.Event()
        self._stall_watchdog: Optional[threading.Thread] = None
        self._stall_poll_interval_s = 0.5
        # 分接递入时机 = 写声卡时刻：write 在声卡缓冲满时按实时阻塞（背压），
        # 紧贴 write 的递入因此跟随播放节奏。随"到达"递入则与听感脱节——
        # 合成停顿、整段突发都会让 sink 跳到句尾，短句句身整段被消费方
        # 跳过（口型不动的根因）
        self._PLAYBACK_FEED_PIECE_S = 0.05

        if not DEPENDENCIES_OK:
            self.logger.error("音频依赖缺失，请安装: pip install sounddevice soundfile")

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
    ) -> None:
        """
        播放音频

        Args:
            audio_array: 音频数据数组
            samplerate: 采样率（默认使用 self.sample_rate）
            device_index: 设备索引（默认使用 self.output_device_index）
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
        self._sink_start()

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

        except Exception as e:
            self.logger.exception(f"音频播放失败: {e}")
            self.is_playing = False
            self._sink_stop()
            raise

    def open_output_stream(self) -> Optional[Any]:
        """打开一个声卡输出流（不开播、不碰分接）；打不开时记日志返回 None。

        打开声卡在 Windows 上要几十到上百毫秒，在事件循环线程里调用会让同一
        循环上的一切顿一下（形象每句开口都卡一帧），事件循环里的调用方应放到
        工作线程，再用 :meth:`start_prepared_stream` 开始这一句。
        """
        if not DEPENDENCIES_OK:
            self.logger.error("sounddevice 库不可用")
            return None
        try:
            return sd.OutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                device=self.output_device_index,
            )
        except Exception as e:
            self.logger.error(f"打开声卡输出流失败: {e}")
            return None

    def start_stream(self, utterance_id: str = "") -> None:
        """启动流式播放：创建声卡流但暂不开播，等预缓冲攒够再启动（同步打开声卡，见 open_output_stream）。

        上游块先入 pending；攒满 ``prebuffer_seconds`` 后由 ``write_chunk``
        统一冲入声卡。短句（总量不足预缓冲）在 ``stop_stream`` 收尾冲播。
        """
        self.start_prepared_stream(self.open_output_stream(), utterance_id)

    def start_prepared_stream(self, stream: Optional[Any], utterance_id: str = "") -> None:
        """用已打开的声卡流开始一句（``stream`` 为 None 时这一句不出声）。

        分接会话在这里同步开启：口型分析器要在事件循环里起后台任务，所以本方法
        要在事件循环线程调用（慢的打开声卡已经在 open_output_stream 里做完）。
        """
        if stream is None:
            # 声卡没打开：清理分接，不把失败路径当作成功播放
            self._stream = None
            self._sink_stop()
            return
        try:
            self._stream = stream
            self._pending_chunks = []
            self._pending_samples = 0
            self._stream_started = False
            self._last_feed_monotonic = None
            self._last_write_block_sec = 0.0
            self.is_playing = True
            # 分接：开播同步开 session（流式播法在写声卡时逐块递入）
            self._sink_start(utterance_id)
            self._start_playback_session()
            self.logger.debug("流式播放已就绪（等待预缓冲）")
        except Exception as e:
            self.logger.error(f"启动流式播放失败: {e}")
            self._stream = None
            self._sink_stop()

    # -----播放中断流检测（播放会话期间的上游供给监控）-----

    def _now(self) -> float:
        """当前单调时刻（秒）。注入时钟优先，缺省取系统单调时钟。"""
        if self._clock is not None:
            return self._clock()
        return time.monotonic()

    def _record_block_arrival(self) -> None:
        """记录一个上游音频块的到达时刻（断流判定的基线）。"""
        self._last_block_monotonic = self._now()
        self._backpressure_at_last_block = self._backpressure_total

    def _start_playback_session(self) -> None:
        """开启播放会话监控：会话期间上游应有块持续到达。

        基线取会话起点——上游整句一个块都不送时同样落在判定范围内
        （这正是旧告警的盲区：写块函数不被调用，旧告警根本不会触发）。
        """
        self._playback_session_active = True
        self._stall_alerted = False
        self._backpressure_total = 0.0
        self._record_block_arrival()
        if not self.stall_watchdog_enabled:
            return
        self._stall_stop_event.clear()
        self._stall_watchdog = threading.Thread(target=self._watchdog_loop, name="audio-stall-watchdog", daemon=True)
        self._stall_watchdog.start()

    def _stop_playback_session(self) -> None:
        """关闭播放会话监控（句间静默期不判断流）。"""
        self._playback_session_active = False
        self._stall_stop_event.set()
        watchdog = self._stall_watchdog
        self._stall_watchdog = None
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=1.0)

    def _watchdog_loop(self) -> None:
        """后台巡检：定时判定播放会话是否断流。

        上游完全无数据时写块函数不会被调用，断流只能靠定时巡检发现——
        这是旧告警在 s59 断流期零告警的根因。
        """
        while not self._stall_stop_event.wait(self._stall_poll_interval_s):
            try:
                self.check_playback_stall()
            except Exception as e:  # noqa: BLE001 - 巡检线程不得因单次异常退出
                self.logger.error(f"播放断流巡检异常: {e}")

    def check_playback_stall(self) -> bool:
        """检测播放中断流：播放会话进行中且超过阈值没有新音频块即告警。

        判定锚定"正在播放"状态而非"连接存活"——句间静默期没有播放会话，
        不产生告警。写声卡的背压时长（数据过剩造成的等待）从间隔中扣除，
        否则数据过剩会被误报成上游断供。

        Returns:
            本次调用是否发出了告警。
        """
        if not self._playback_session_active or self._last_block_monotonic is None:
            return False
        idle_sec = (self._now() - self._last_block_monotonic) - (
            self._backpressure_total - self._backpressure_at_last_block
        )
        if idle_sec * 1000 < self.stall_threshold_ms:
            return False
        if self._stall_alerted:
            return False
        self._stall_alerted = True
        self.logger.warning(f"播放中断流：正在播放但已 {idle_sec:.1f}s 未收到新音频块（TTS 上游供给中断）")
        return True

    def _flush_pending(self) -> None:
        """把预缓冲冲入声卡：首次冲入时启动流，随后逐块写入。"""
        if self._stream is None:
            return
        if not self._stream_started:
            self._stream.start()
            self._stream_started = True
            self.logger.debug(f"预缓冲达阈值（{self.prebuffer_seconds:.1f}s），声卡流开播")
        t0 = time.perf_counter()
        for chunk in self._pending_chunks:
            # 分接紧贴写声卡：冲播批次写入时按实时阻塞（缓冲满后），递入
            # 因此与听感同步——短句兜底冲播的整段也在这里逐块递给分接
            self._sink_feed(chunk, self.sample_rate)
            self._stream.write(chunk)
        self._last_write_block_sec = time.perf_counter() - t0
        self._backpressure_total += self._last_write_block_sec
        self._pending_chunks = []
        self._pending_samples = 0

    def write_chunk(self, chunk: np.ndarray) -> None:
        """向流写入一个音频块,立即输出到扬声器。

        预缓冲未满时块入 pending（声卡流保持未启动）；攒够阈值统一冲入。
        此后各块直接写声卡（write 在缓冲满时按实时节奏阻塞，形成背压）。

        Args:
            chunk: 音频数据块 (1D 或 2D ndarray)
        """
        # 分接紧贴写声卡（见 _flush_pending 内注释）；预缓冲期的块未进声卡、
        # 不递——随到达递入会让 sink 跳到句尾，短句句身整段被消费方跳过
        if not DEPENDENCIES_OK:
            return
        if self._stream is None:
            return
        try:
            now = time.monotonic()
            if self._last_feed_monotonic is not None:
                net_gap = (now - self._last_feed_monotonic) - self._last_write_block_sec
                if net_gap > 2.0:
                    # 块间隔偏大只作诊断信息：逐句系统性触发，与听感断流无关，
                    # 不进告警面（真断流由播放中断流检测负责）
                    self.logger.debug(
                        f"上游块间隔偏大 {net_gap:.1f}s（已扣除写背压 {self._last_write_block_sec:.1f}s）"
                    )
            self._last_feed_monotonic = now
            self._record_block_arrival()
            if not self._stream_started:
                self._pending_chunks.append(chunk)
                self._pending_samples += len(chunk)
                if self._pending_samples >= int(self.prebuffer_seconds * self.sample_rate):
                    self._flush_pending()
            else:
                self._sink_feed(chunk, self.sample_rate)
                t0 = time.perf_counter()
                self._stream.write(chunk)
                self._last_write_block_sec = time.perf_counter() - t0
                self._backpressure_total += self._last_write_block_sec
        except Exception as e:
            self.logger.error(f"写入音频流失败: {e}")

    def stop_stream(self, discard_pending: bool = False) -> None:
        """停止并关闭流式播放。

        Args:
            discard_pending: True 时直接丢弃未冲播的预缓冲（取消/截断语义，
                避免 buffer 残留与下一次发声重叠）；默认 False 先冲播残余
                （正常收尾，短句兜底）。
        """
        if not DEPENDENCIES_OK:
            return
        if self._stream is None:
            return
        try:
            if self._pending_chunks and not discard_pending:
                self._flush_pending()
            self._stream.stop()
            self._stream.close()
        except Exception as e:
            self.logger.warning(f"停止音频流失败: {e}")
        finally:
            self._stream = None
            self._pending_chunks = []
            self._pending_samples = 0
            self._stream_started = False
            self._last_feed_monotonic = None
            self._last_write_block_sec = 0.0
            self.is_playing = False
            # 分接：播完同步收 session
            self._sink_stop()
            # 播完即退出断流监控：句间静默期不判断流
            self._stop_playback_session()
            self.logger.debug("流式播放已停止")

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
