"""
LipSyncProcessor - VTS 口型同步处理器

负责音频流处理、元音检测与 VTS 口型参数映射。
"""

import asyncio
import time
from typing import Any, Callable, Coroutine, Dict, Optional

from src.modules.logging import get_logger


class LipSyncProcessor:
    """口型同步处理器"""

    def __init__(
        self,
        *,
        logger_name: str,
        sample_rate: int,
        volume_threshold: float,
        smoothing_factor: float,
        vowel_detection_sensitivity: float,
        vts_set_parameter: Callable[..., Coroutine[Any, Any, bool]],
        is_connected: Callable[[], bool],
        volume_gain: float = 1.0,
        max_mouth_open: float = 0.85,
        silence_threshold: float = 0.02,
        close_mouth_threshold: float = 0.06,
        power_curve: float = 0.6,
        vowel_open_weight: float = 0.7,
        update_interval_ms: float = 30.0,
        analysis_window_ms: float = 60.0,
        loop_interval_ms: float = 40.0,
        expression_fade_speed: float = 0.15,
        mouth_open_lerp_speed: float = 0.35,
        vowel_decay: float = 0.4,
        min_mouth_delta: float = 0.005,
        expression_rest_values: Optional[Dict[str, float]] = None,
    ):
        self._logger_name = logger_name
        self.logger = get_logger(logger_name)
        self._sample_rate = sample_rate
        self._volume_threshold = volume_threshold
        self._smoothing_factor = smoothing_factor
        self._vowel_detection_sensitivity = vowel_detection_sensitivity
        self._set_parameter = vts_set_parameter
        self._is_connected = is_connected

        # 口型自然度参数
        self._volume_gain = volume_gain
        self._max_mouth_open = max_mouth_open
        self._silence_threshold = silence_threshold
        self._close_mouth_threshold = close_mouth_threshold
        self._power_curve = power_curve
        self._vowel_open_weight = vowel_open_weight
        self._update_interval = update_interval_ms / 1000.0

        # 分析窗口与循环间隔
        self._analysis_window_seconds = analysis_window_ms / 1000.0
        self._loop_interval = loop_interval_ms / 1000.0
        self._max_buffer_seconds = 2.0  # 保留最近 2 秒音频避免内存无限增长

        # 表情基础值与淡出速度
        self._expression_fade_speed = expression_fade_speed
        self._base_expressions: Dict[str, float] = {}
        self._current_expression_values: Dict[str, float] = {}
        # 各表情参数的"静止值"：说完话淡出到这个值而不是 0。
        # 例如 EyeOpen* 的静止值必须是 1.0（睁眼），0 会变成闭眼；
        # MouthSmile 可配置为常驻微笑基线，避免说完话瞬间变"撇嘴"。
        self._expression_rest_values: Dict[str, float] = dict(expression_rest_values or {})
        self._last_expression_update_time = 0.0
        self._expression_update_interval = update_interval_ms / 1000.0

        # 嘴型平滑参数
        self._mouth_open_lerp_speed = mouth_open_lerp_speed
        self._vowel_decay = vowel_decay
        self._min_mouth_delta = min_mouth_delta
        self._target_mouth_open = 0.0

        self.is_speaking = False
        self.current_vowel_values: Dict[str, float] = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0
        self.current_mouth_open = 0.0
        self._target_mouth_open = 0.0
        self.audio_analysis_lock = asyncio.Lock()

        self.vowel_formants = {
            "A": [730, 1090],
            "I": [270, 2290],
            "U": [300, 870],
            "E": [530, 1840],
            "O": [570, 840],
        }

        self.accumulated_audio = bytearray()
        self.accumulation_start_time: Optional[float] = None
        self.audio_playback_start_time: Optional[float] = None
        self._last_update_time = 0.0
        self._lip_sync_task: Optional[asyncio.Task] = None

    def _rest_value(self, name: str) -> float:
        """参数的静止值（未配置时为 0.0）"""
        return self._expression_rest_values.get(name, 0.0)

    def set_base_expressions(self, expressions: Dict[str, float]) -> None:
        """设置说话时保持的基础表情（如 MouthSmile），会自动过滤 MouthOpen"""
        filtered = {}
        for name, value in (expressions or {}).items():
            if name == "MouthOpen":
                continue
            filtered[name] = float(value)
        self._base_expressions = filtered
        # 初始化当前值（从静止值起步），让后续平滑过渡
        for name, _value in filtered.items():
            self._current_expression_values.setdefault(name, self._rest_value(name))
        self.logger.debug(f"LipSync 基础表情已设置: {filtered}")

    async def start_session(self, text: str = "") -> None:
        if self.is_speaking:
            await self.stop_session()

        self.is_speaking = True
        self.audio_playback_start_time = time.time()
        self.accumulation_start_time = time.time()
        self.accumulated_audio = bytearray()
        self.current_vowel_values = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0
        self.current_mouth_open = 0.0
        self._target_mouth_open = 0.0
        self._last_update_time = 0.0
        self._last_expression_update_time = 0.0

        # 启动后台口型更新循环，按真实时间持续分析音频
        self._lip_sync_task = asyncio.create_task(self._lip_sync_loop())
        self._lip_sync_task.set_name(f"{self._logger_name}.lip_sync_loop")

    async def process_audio(self, audio_data: bytes, sample_rate: int = 32000) -> None:
        if not self.is_speaking:
            return
        # 仅快速追加数据，实际分析在独立后台循环中进行，避免阻塞音频流
        async with self.audio_analysis_lock:
            self.accumulated_audio.extend(audio_data)
            self._trim_audio_buffer()

    def _trim_audio_buffer(self) -> None:
        """仅保留最近一段时间的音频，防止内存无限增长"""
        max_bytes = int(self._max_buffer_seconds * self._sample_rate * 2)  # int16 = 2 bytes
        if len(self.accumulated_audio) > max_bytes:
            self.accumulated_audio = self.accumulated_audio[-max_bytes:]

    def _has_active_expressions(self) -> bool:
        """是否还有未回到静止值的表情，用于停止后继续淡出"""
        return any(abs(v - self._rest_value(name)) > 0.01 for name, v in self._current_expression_values.items())

    async def _lip_sync_loop(self) -> None:
        """后台口型更新循环：按真实时间定期分析最近音频"""
        try:
            # 即使 is_speaking 结束，也继续运行直到表情淡出完成
            while self.is_speaking or self._has_active_expressions():
                await self._analyze_audio_state()
                await asyncio.sleep(self._loop_interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"口型同步后台循环异常: {e}", exc_info=True)

    async def _analyze_audio_state(self) -> None:
        volume = 0.0
        vowel_values: Dict[str, float] = {}

        if self.is_speaking:
            try:
                import numpy as np
            except ImportError:
                await self._update_lip_sync_parameters(volume, vowel_values)
                return

            async with self.audio_analysis_lock:
                buffer_len = len(self.accumulated_audio)
                if buffer_len >= 1024:
                    # 只分析最近一个窗口的音频，反应当前播放位置
                    window_bytes = int(self._analysis_window_seconds * self._sample_rate * 2)
                    audio_bytes = bytes(self.accumulated_audio[-window_bytes:])

                    audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
                    audio_array = audio_array / 32768.0

                    rms = float(np.sqrt(np.mean(audio_array**2)))
                    volume = min(1.0, rms * 6)

                    if volume > self._volume_threshold * 0.3:
                        vowel_values = self._detect_vowels(audio_array, sample_rate=self._sample_rate)

        await self._update_lip_sync_parameters(volume, vowel_values)

    def _detect_vowels(self, audio_array, sample_rate: int = 16000) -> Dict[str, float]:
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

    async def _update_base_expressions(self, volume: float) -> None:
        """说话时维持基础表情，静音或结束时向 0 淡出"""
        if not self._base_expressions and not self._current_expression_values:
            return

        now = time.time()
        if now - self._last_expression_update_time < self._expression_update_interval:
            return
        self._last_expression_update_time = now

        # 说话时且音量足够，目标为基础表情值；否则淡出到静止值（眼睁/常驻微笑，而非一律归零）
        if self.is_speaking and volume >= self._silence_threshold:
            targets = self._base_expressions
            speed = 0.35  # 说话期间较快达到目标
        else:
            targets = {name: self._rest_value(name) for name in self._current_expression_values}
            speed = self._expression_fade_speed  # 静音/结束时较慢淡出

        for name, target in targets.items():
            current = self._current_expression_values.get(name, 0.0)
            if abs(target - current) < 0.01:
                new_value = target
            else:
                new_value = current + (target - current) * speed

            self._current_expression_values[name] = new_value
            if abs(new_value - current) >= 0.005:
                await self._set_parameter(name, new_value, weight=1)

    async def _update_lip_sync_parameters(self, volume: float, vowel_values: Dict[str, float]) -> None:
        now = time.time()

        # 同步更新基础表情（说话保持/静音淡出），与口型更新频率独立
        await self._update_base_expressions(volume)

        # 限制 VTS 参数更新频率，避免过于抖动
        if now - self._last_update_time < self._update_interval:
            return
        self._last_update_time = now

        # 1. 先衰减已有元音值，让嘴巴能在音节间隙闭上
        #    使用可配置衰减系数，较慢衰减让元音嘴型更连续
        for vowel in self.current_vowel_values:
            self.current_vowel_values[vowel] *= self._vowel_decay

        # 2. 更新检测到的元音（取较大值，保持瞬时响应）
        for vowel, value in vowel_values.items():
            smoothed = self._smoothing_factor * value + (1 - self._smoothing_factor) * self.current_vowel_values.get(
                vowel, 0
            )
            self.current_vowel_values[vowel] = max(self.current_vowel_values[vowel], smoothed)

        # 3. 静音检测：音量极低时直接闭嘴
        if volume < self._silence_threshold:
            if self.current_mouth_open > 0.02:
                await self._set_parameter("MouthOpen", 0.0, weight=1)
                self.current_mouth_open = 0.0
                self.logger.info(f"LipSync MouthOpen=0.000 (silence, volume={volume:.3f})")
            self.current_volume = volume
            return

        # 4. 计算基于音量的张嘴幅度
        #    使用幂曲线 + max_mouth_open 缩放，让嘴型更多分布在 0~0.5 区间，
        #    只有强音才接近最大张嘴，避免长时间大张。
        scaled_volume = min(1.0, volume * self._volume_gain)
        volume_open = (scaled_volume**self._power_curve) * self._max_mouth_open

        # 5. 计算元音张嘴幅度，只取开口元音 A/O，并受音量抑制
        vowel_open = max(
            self.current_vowel_values.get("A", 0),
            self.current_vowel_values.get("O", 0),
        )
        vowel_open *= self._vowel_open_weight * (0.3 + 0.7 * volume)

        # 6. 结合音量和元音
        mouth_open = max(volume_open, vowel_open)

        # 7. 低音量时额外衰减，让句子中的气口/停顿自然闭嘴
        if volume < self._close_mouth_threshold:
            mouth_open *= 0.2 + 0.8 * (volume / self._close_mouth_threshold)

        # 8. 限制最大张嘴幅度
        mouth_open = min(self._max_mouth_open, mouth_open)
        self._target_mouth_open = mouth_open

        # 9. 平滑过渡：每帧向目标值插值，避免嘴型跳变
        lerp_speed = self._mouth_open_lerp_speed
        # 闭嘴比张嘴稍快，让气口更干净
        if mouth_open < self.current_mouth_open:
            lerp_speed = min(0.8, lerp_speed * 1.6)
        smoothed = self.current_mouth_open + (mouth_open - self.current_mouth_open) * lerp_speed

        # 只有当变化足够大时才发送，但阈值很小以保证连续性
        if abs(smoothed - self.current_mouth_open) < self._min_mouth_delta:
            self.current_volume = volume
            return

        success = await self._set_parameter("MouthOpen", smoothed, weight=1)
        self.current_mouth_open = smoothed
        self.current_volume = volume
        self.logger.info(
            f"LipSync MouthOpen={smoothed:.3f} (target={mouth_open:.3f}, volume={volume:.3f}, vowel={vowel_open:.3f}) success={success}"
        )

    async def stop_session(self) -> None:
        if not self.is_speaking:
            return
        self.is_speaking = False

        # 让后台循环继续运行，完成表情淡出
        if self._lip_sync_task and not self._lip_sync_task.done():
            try:
                # 最多等待 2 秒让表情淡出完成
                await asyncio.wait_for(self._lip_sync_task, timeout=2.0)
            except asyncio.TimeoutError:
                self._lip_sync_task.cancel()
                try:
                    await self._lip_sync_task
                except asyncio.CancelledError:
                    pass
            except Exception:
                pass
        self._lip_sync_task = None

        # 确保嘴巴归零、表情回到静止值（EyeOpen 保持睁眼、MouthSmile 保持常驻微笑）
        self.current_vowel_values = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0
        self.current_mouth_open = 0.0
        self._target_mouth_open = 0.0
        await self._set_parameter("MouthOpen", 0.0, weight=1)
        for name in list(self._current_expression_values.keys()):
            rest = self._rest_value(name)
            if abs(self._current_expression_values[name] - rest) > 0.005:
                await self._set_parameter(name, rest, weight=1)
            self._current_expression_values[name] = rest
        self.accumulated_audio = bytearray()
        self.accumulation_start_time = None
        self.audio_playback_start_time = None
