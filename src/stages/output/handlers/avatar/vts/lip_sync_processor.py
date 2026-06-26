"""
LipSyncProcessor - VTS 口型同步处理器

负责音频流处理、元音检测与 VTS 口型参数映射。
"""

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, Optional

from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.streaming.audio_chunk import AudioChunk, AudioMetadata


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
    ):
        self.logger = get_logger(logger_name)
        self._sample_rate = sample_rate
        self._volume_threshold = volume_threshold
        self._smoothing_factor = smoothing_factor
        self._vowel_detection_sensitivity = vowel_detection_sensitivity
        self._set_parameter = vts_set_parameter
        self._is_connected = is_connected

        self.is_speaking = False
        self.current_vowel_values: Dict[str, float] = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0
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

    async def on_start(self, metadata: "AudioMetadata") -> None:
        """AudioStreamChannel: 音频流开始回调"""
        if not self._is_connected():
            return
        self.logger.debug(f"收到音频流开始通知: {metadata.text[:30]}...")
        await self.start_session(metadata.text)

    async def on_chunk(self, chunk: "AudioChunk") -> None:
        """AudioStreamChannel: 音频块回调"""
        if not self._is_connected():
            return
        try:
            from src.modules.streaming.audio_utils import resample_audio

            audio_data = resample_audio(chunk.data, chunk.sample_rate, self._sample_rate)
            await self.process_audio(audio_data, self._sample_rate)
        except Exception as e:
            self.logger.error(f"处理音频块失败: {e}")

    async def on_end(self, metadata: "AudioMetadata") -> None:
        """AudioStreamChannel: 音频流结束回调"""
        if not self._is_connected():
            return
        self.logger.debug("收到音频流结束通知")
        await self.stop_session()

    async def start_session(self, text: str = "") -> None:
        if self.is_speaking:
            await self.stop_session()

        import time

        self.is_speaking = True
        self.audio_playback_start_time = time.time()
        self.accumulation_start_time = time.time()
        self.accumulated_audio = bytearray()
        self.current_vowel_values = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0

    async def process_audio(self, audio_data: bytes, sample_rate: int = 32000) -> None:
        if not self.is_speaking:
            return
        async with self.audio_analysis_lock:
            self.accumulated_audio.extend(audio_data)
            await self._analyze_audio_state()

    async def _analyze_audio_state(self) -> None:
        if len(self.accumulated_audio) < 1024:
            return

        try:
            import numpy as np

            audio_array = np.frombuffer(self.accumulated_audio, dtype=np.int16).astype(np.float32)
            audio_array = audio_array / 32768.0

            rms = float(np.sqrt(np.mean(audio_array**2)))
            volume = min(1.0, rms * 10)

            if volume > self._volume_threshold:
                vowel_values = self._detect_vowels(audio_array, sample_rate=self._sample_rate)
                await self._update_lip_sync_parameters(volume, vowel_values)

            self.accumulated_audio = bytearray()
            self.accumulation_start_time = None
        except ImportError:
            pass

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

    async def _update_lip_sync_parameters(self, volume: float, vowel_values: Dict[str, float]) -> None:
        for vowel, value in vowel_values.items():
            new_value = self._smoothing_factor * value + (1 - self._smoothing_factor) * self.current_vowel_values.get(
                vowel, 0
            )
            self.current_vowel_values[vowel] = new_value

        if "A" in self.current_vowel_values or "O" in self.current_vowel_values:
            mouth_open = max(self.current_vowel_values.get("A", 0), self.current_vowel_values.get("O", 0))
            await self._set_parameter("MouthOpen", mouth_open, weight=1)

        self.current_volume = volume

    async def stop_session(self) -> None:
        if not self.is_speaking:
            return
        self.is_speaking = False
        self.current_vowel_values = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0
        await self._set_parameter("MouthOpen", 0.0, weight=1)
        self.accumulated_audio = bytearray()
        self.accumulation_start_time = None
        self.audio_playback_start_time = None
