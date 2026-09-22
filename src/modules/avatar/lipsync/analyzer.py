"""LipSyncAnalyzer - 口型分析器（共享 · 平台无关）

把 TTS 播放路径分接来的音频（``AudioSink`` 协议形状：``start → feed×N →
stop``）分析成平台无关的口型信号（``MouthSignal``：嘴张开量 + 元音成分
A/I/U/E/O + 音量），分发给注册的渲染器（VTS / Warudo / VRChat 各自把
信号翻译为本平台参数）。

边界：

- **只分析不渲染**——写平台参数归各平台渲染器（VTS 的表情维护 /
  ``MouthOpen`` 写入都在 VTS 渲染器内），本件零平台参数名；
- **非阻塞**——``feed`` 只追加缓冲立即返回（不得拖慢播放写盘），分析
  在后台循环按真实时间游标推进；
- **一次发言一次会话**——播放器开播/播完同步开关会话（不走事件广播，
  广播是事后通知、拿不到正在播的音频）；停止后嘴部信号收静止值；
- **fail-soft**——渲染器异常吞掉记日志，不影响播放与其他渲染器。

调参来自 ``avatar.toml`` 顶层 ``[lipsync]`` 段：口型分析器是共享基础
设施、非 provider，调参不进 provider 命名空间。
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.logging import get_logger

# 静音/低音量需持续此时长才执行闭嘴：真实语音的音节间隙（百毫秒级）若
# 立即压嘴会表现为嘴部高频塌陷抖动；短暂低音量期间保持当前张嘴度。
_SILENCE_CLOSE_HOLD_SECONDS = 0.2

# RMS → 音量的满刻度参考：典型 TTS 输出 RMS 在 0.03–0.1 量级，
# 该值决定多大声算"满响度"（映射到 max_mouth_open）。
_FULL_VOLUME_RMS = 0.083

if TYPE_CHECKING:
    import numpy as np


class LipSyncConfig(BaseConfig):
    """口型分析调参（TOML 段位：avatar.toml 顶层 ``[lipsync]``）

    键名沿用历史 VTS 配置命名（跨文件搬迁不改键，行为保真）；
    ``*_ms`` 命名实际单位是 float 秒（历史形态，保持）。
    """

    type: str = "lipsync"
    enabled: bool = Field(default=True, description="是否启用口型分析（关 = 不构造分析器、不分接播放音频）")
    sample_rate: int = Field(default=16000, ge=8000, le=48000, description="口型分析采样率 Hz")
    volume_threshold: float = Field(default=0.01, ge=0.0, description="音量阈值")
    smoothing_factor: float = Field(default=0.3, ge=0.0, le=1.0, description="平滑系数")
    vowel_detection_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0, description="元音检测灵敏度")
    volume_gain: float = Field(default=1.0, ge=0.0, description="音量增益")
    max_mouth_open: float = Field(default=0.6, ge=0.0, le=1.0, description="最大张嘴度")
    silence_threshold: float = Field(default=0.02, ge=0.0, description="静音阈值")
    close_mouth_threshold: float = Field(default=0.06, ge=0.0, description="闭嘴阈值（低于此值触发闭嘴）")
    power_curve: float = Field(default=1.0, ge=0.0, description="功率曲线指数")
    vowel_open_weight: float = Field(default=0.5, ge=0.0, description="元音张嘴权重")
    update_interval_ms: float = Field(default=30.0, ge=0.0, description="信号更新间隔（秒；命名沿用）")
    mouth_open_lerp_speed: float = Field(default=0.35, ge=0.0, description="张嘴插值速度")
    vowel_decay: float = Field(default=0.4, ge=0.0, description="元音衰减")
    min_mouth_delta: float = Field(default=0.005, ge=0.0, description="最小张嘴变化阈值")


class MouthSignal(BaseModel):
    """口型信号（平台无关的分析产物）

    Attributes:
        mouth_open: 嘴张开量（0.0–1.0 归一化）。
        vowels: 元音成分 A/I/U/E/O（0.0–1.0，键为元音字母）。
        volume: 音量（0.0–1.0 归一化）。
        utterance_id: 关联的发声实例 ID（会话开始时携带）。
    """

    mouth_open: float = Field(default=0.0, ge=0.0, le=1.0, description="嘴张开量（0.0–1.0）")
    vowels: Dict[str, float] = Field(
        default_factory=lambda: {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0},
        description="元音成分 A/I/U/E/O（0.0–1.0）",
    )
    volume: float = Field(default=0.0, ge=0.0, le=1.0, description="音量（0.0–1.0）")
    utterance_id: str = Field(default="", description="关联的发声实例 ID")


class LipSyncRenderer:
    """口型渲染器基接口（协议形状；各平台自行实现）。

    渲染器把平台无关的 ``MouthSignal`` 翻译为本平台参数写入。接口为
    async（渲染写入多为异步平台调用）；异常由分析循环捕获（fail-soft）。
    """

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        """处理一帧口型信号（写本平台嘴部参数）。"""
        raise NotImplementedError

    async def on_session_end(self) -> None:
        """会话结束：把嘴部收到本平台的静止值。"""
        return None


class LipSyncAnalyzer:
    """口型分析器（共享 · 平台无关；实现 ``AudioSink`` 会话形状）"""

    def __init__(
        self,
        *,
        logger_name: str = "avatar.lipsync.Analyzer",
        config: Optional[Dict[str, Any]] = None,
        renderers: Optional[List[LipSyncRenderer]] = None,
    ) -> None:
        self._logger_name = logger_name
        self.logger = get_logger(logger_name)

        # 调参（typed；空 dict = 全默认；失败 log+raise）
        try:
            self.typed_config = LipSyncConfig.from_dict(config or {})
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise
        cfg = self.typed_config
        self._sample_rate = cfg.sample_rate
        self._volume_threshold = cfg.volume_threshold
        self._smoothing_factor = cfg.smoothing_factor
        self._vowel_detection_sensitivity = cfg.vowel_detection_sensitivity
        self._volume_gain = cfg.volume_gain
        self._max_mouth_open = cfg.max_mouth_open
        self._silence_threshold = cfg.silence_threshold
        self._close_mouth_threshold = cfg.close_mouth_threshold
        self._power_curve = cfg.power_curve
        self._vowel_open_weight = cfg.vowel_open_weight
        self._update_interval = cfg.update_interval_ms / 1000.0
        self._mouth_open_lerp_speed = cfg.mouth_open_lerp_speed
        self._vowel_decay = cfg.vowel_decay
        self._min_mouth_delta = cfg.min_mouth_delta

        # 分析窗口与循环间隔；缓冲上限（保留最近 2 秒音频避免内存无限增长）
        self._analysis_window_seconds = 0.06
        self._loop_interval = 0.04
        self._max_buffer_seconds = 2.0

        self._renderers: List[LipSyncRenderer] = list(renderers or [])

        # 会话状态
        self.is_speaking = False
        self.current_utterance_id: str = ""
        self._audio_buffer = bytearray()
        self._analysis_task: Optional[asyncio.Task] = None
        self._buffer_sample_rate: int = self._sample_rate

        # 最近一帧信号（观察面 / 测试）
        self.last_signal = MouthSignal()
        self._last_emit_time = 0.0
        # 低音量持续起点（None = 当前不处于低音量）；用于静音闭嘴的持续判定
        self._low_volume_since: Optional[float] = None

        self.vowel_formants = {
            "A": [730, 1090],
            "I": [270, 2290],
            "U": [300, 870],
            "E": [530, 1840],
            "O": [570, 840],
        }

    # ==================== 渲染器注册（一个分析器扇出多具皮套） ====================

    def add_renderer(self, renderer: LipSyncRenderer) -> None:
        """注册一个平台渲染器（重复注册幂等）。"""
        if renderer not in self._renderers:
            self._renderers.append(renderer)
            self.logger.debug(f"口型渲染器已注册: {type(renderer).__name__}（共 {len(self._renderers)}）")

    def remove_renderer(self, renderer: LipSyncRenderer) -> None:
        """注销渲染器（cleanup 时调用；未注册时静默）。"""
        if renderer in self._renderers:
            self._renderers.remove(renderer)

    # ==================== AudioSink 会话形状（同步、非阻塞） ====================

    def start(self, utterance_id: str = "") -> None:
        """会话开始（播放器开播时同步调用）。

        复位游标与缓冲、启动后台分析循环。若上一会话未正常结束，先收尾。
        """
        if self.is_speaking:
            self.stop()

        self.is_speaking = True
        self.current_utterance_id = utterance_id
        self._audio_buffer = bytearray()
        self.last_signal = MouthSignal(utterance_id=utterance_id)
        self._last_emit_time = 0.0

        self._analysis_task = asyncio.create_task(self._analysis_loop())
        self._analysis_task.set_name(f"{self._logger_name}.analysis_loop")

    def feed(self, chunk: "np.ndarray", sample_rate: int) -> None:
        """递入一个音频块（同步、非阻塞：只追加缓冲立即返回）。

        引擎播放器的 dtype 形态不一（float32 归一化 / int16），统一转
        int16 字节缓冲；实际分析在后台循环进行。
        """
        if not self.is_speaking:
            return
        try:
            import numpy as np

            array = np.asarray(chunk)
            if array.ndim > 1:  # 多声道 → 取均值降为单声道
                array = array.mean(axis=1) if array.shape[0] < array.shape[-1] else array.mean(axis=-1)
            if array.dtype != np.int16:
                if np.issubdtype(array.dtype, np.floating):
                    array = np.clip(array, -1.0, 1.0) * 32767.0
                array = array.astype(np.int16)
            self._buffer_sample_rate = int(sample_rate)
            self._audio_buffer.extend(array.astype(np.int16).tobytes())
            self._trim_audio_buffer()
        except Exception as e:
            self.logger.warning(f"口型缓冲追加失败（已忽略）: {e}")

    def stop(self) -> None:
        """会话结束（播放器播完时同步调用）。

        置结束标志；后台循环发出收尾静止信号后自行退出（限期等待经
        ``wait_finished`` 由需要方选用，不阻塞播放路径）。
        """
        if not self.is_speaking:
            return
        self.is_speaking = False

    async def wait_finished(self, timeout: float = 2.0) -> None:
        """等待后台分析循环退出（含静止信号分发）；超时强制取消。"""
        task = self._analysis_task
        if task is None or task.done():
            self._analysis_task = None
            return
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning(f"等待口型收尾异常（已忽略）: {exc}")
        self._analysis_task = None

    # ==================== 内部分析 ====================

    def _trim_audio_buffer(self) -> None:
        """仅保留最近一段时间的音频，防止内存无限增长"""
        max_bytes = int(self._max_buffer_seconds * self._buffer_sample_rate * 2)  # int16 = 2 bytes
        if len(self._audio_buffer) > max_bytes:
            self._audio_buffer = self._audio_buffer[-max_bytes:]

    def _has_pending_audio(self) -> bool:
        return len(self._audio_buffer) >= 1024

    async def _analysis_loop(self) -> None:
        """后台分析循环：按真实时间游标定期分析最近音频窗口，结束后收静止。"""
        try:
            while self.is_speaking or self._has_pending_audio():
                await self._analyze_and_dispatch()
                await asyncio.sleep(self._loop_interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception(f"口型分析循环异常: {e}")
        finally:
            # 会话收尾：静止信号（嘴收到 0）分发给全部渲染器
            rest = MouthSignal(utterance_id=self.current_utterance_id)
            self.last_signal = rest
            await self._dispatch(rest)
            for renderer in list(self._renderers):
                try:
                    await renderer.on_session_end()
                except Exception as e:
                    self.logger.warning(f"渲染器会话收尾异常（已忽略）: {e}")

    async def _analyze_and_dispatch(self) -> None:
        volume = 0.0
        vowel_values: Dict[str, float] = {}

        if self.is_speaking:
            try:
                import numpy as np
            except ImportError:
                await self._emit(volume, vowel_values)
                return

            # 缓冲读写都在事件循环线程内同步完成（feed 非阻塞、此处切片
            # 无 await 点），无交错可能，不需要加锁
            buffer_len = len(self._audio_buffer)
            if buffer_len >= 1024:
                # 只分析最近一个窗口的音频，反应当前播放位置（真实时间游标）；
                # 窗口换算用实际喂入采样率（feed 报告值），与配置值解耦
                window_bytes = int(self._analysis_window_seconds * self._buffer_sample_rate * 2)
                audio_bytes = bytes(self._audio_buffer[-window_bytes:])

                audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
                audio_array = audio_array / 32768.0

                rms = float(np.sqrt(np.mean(audio_array**2)))
                volume = min(1.0, rms / _FULL_VOLUME_RMS)

                if volume > self._volume_threshold * 0.3:
                    vowel_values = self._detect_vowels(audio_array, sample_rate=self._buffer_sample_rate)

        await self._emit(volume, vowel_values)

    async def _emit(self, volume: float, vowel_values: Dict[str, float]) -> None:
        """把本帧分析结果平滑成一帧 ``MouthSignal`` 并分发渲染器。"""
        now = time.time()

        # 限制信号更新频率，避免过于抖动
        if now - self._last_emit_time < self._update_interval:
            return
        self._last_emit_time = now

        # 低音量持续判定：短暂低音量（音节间隙）不触发闭嘴/衰减
        if volume >= self._close_mouth_threshold:
            self._low_volume_since = None
        elif self._low_volume_since is None:
            self._low_volume_since = now
        low_volume_held = now - (self._low_volume_since if self._low_volume_since is not None else now)
        sustained_low = low_volume_held >= _SILENCE_CLOSE_HOLD_SECONDS

        current = self.last_signal
        vowels: Dict[str, float] = {}
        for vowel in ("A", "I", "U", "E", "O"):
            incoming = vowel_values.get(vowel, 0.0)
            smoothed = self._smoothing_factor * incoming + (1 - self._smoothing_factor) * current.vowels.get(vowel, 0.0)
            vowels[vowel] = max(current.vowels.get(vowel, 0.0) * self._vowel_decay, smoothed)

        # 静音检测：持续静音才闭嘴；短暂静音保持当前张嘴度
        if volume < self._silence_threshold:
            mouth_open = 0.0 if sustained_low else current.mouth_open
        else:
            scaled_volume = min(1.0, volume * self._volume_gain)
            volume_open = (scaled_volume**self._power_curve) * self._max_mouth_open

            # 元音张嘴幅度只取开口元音 A/O，并受音量抑制
            vowel_open = max(vowels.get("A", 0.0), vowels.get("O", 0.0))
            vowel_open *= self._vowel_open_weight * (0.3 + 0.7 * volume)

            mouth_open = max(volume_open, vowel_open)

            # 低音量额外衰减（让长气口自然闭嘴）：仅对持续低音量生效
            if volume < self._close_mouth_threshold:
                if sustained_low:
                    mouth_open *= 0.2 + 0.8 * (volume / self._close_mouth_threshold)
                else:
                    mouth_open = max(mouth_open, current.mouth_open)

            mouth_open = min(self._max_mouth_open, mouth_open)

        # 平滑过渡：每帧向目标值插值，避免嘴型跳变（闭嘴比张嘴稍快）
        lerp_speed = self._mouth_open_lerp_speed
        if mouth_open < current.mouth_open:
            lerp_speed = min(0.8, lerp_speed * 1.6)
        smoothed_open = current.mouth_open + (mouth_open - current.mouth_open) * lerp_speed
        if abs(smoothed_open - current.mouth_open) < self._min_mouth_delta:
            smoothed_open = current.mouth_open

        signal = MouthSignal(
            mouth_open=round(max(0.0, min(1.0, smoothed_open)), 4),
            vowels={k: round(max(0.0, min(1.0, v)), 4) for k, v in vowels.items()},
            volume=round(min(1.0, max(0.0, volume)), 4),
            utterance_id=self.current_utterance_id,
        )
        self.last_signal = signal
        await self._dispatch(signal)

    async def _dispatch(self, signal: MouthSignal) -> None:
        """分发信号给全部渲染器（单渲染器故障隔离）。"""
        for renderer in list(self._renderers):
            try:
                await renderer.on_mouth_signal(signal)
            except Exception as e:
                self.logger.warning(f"口型渲染器异常（已忽略）: {type(renderer).__name__}: {e}")

    def _detect_vowels(self, audio_array: "np.ndarray", sample_rate: int = 16000) -> Dict[str, float]:
        """共振峰法元音检测（FFT 频带能量）"""
        try:
            import numpy as np
        except ImportError:
            return {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}

        if len(audio_array) < 512:
            return {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}

        fft_result = np.fft.rfft(audio_array)
        magnitude = np.abs(fft_result)
        freqs = np.fft.rfftfreq(len(audio_array), d=1.0 / sample_rate)

        vowel_scores = {}
        for vowel, (f1, f2) in self.vowel_formants.items():
            f1_band = (freqs >= f1 - 100) & (freqs <= f1 + 100)
            f2_band = (freqs >= f2 - 200) & (freqs <= f2 + 200)
            combined_energy = float(np.sum(magnitude[f1_band]) + np.sum(magnitude[f2_band]))
            vowel_scores[vowel] = min(1.0, combined_energy / 1000.0)

        max_score = max(vowel_scores.values()) if vowel_scores else 0
        if max_score > 0:
            for vowel in vowel_scores:
                vowel_scores[vowel] = (vowel_scores[vowel] / max_score) * self._vowel_detection_sensitivity

        return vowel_scores


__all__ = ["LipSyncAnalyzer", "LipSyncConfig", "LipSyncRenderer", "MouthSignal"]
