"""
Warudo 音频口型同步订阅器

基于 VTS 的 LipSyncProcessor 适配,通过 AudioStreamChannel 订阅 TTS 音频流,
将元音检测结果写入 WarudoStateManager 的 MouthState (VowelA/I/U/E/O)。

设计要点:
- 复用 VTS 的 LipSyncProcessor(已完全解耦,通过 is_connected lambda + set_parameter 回调)
- 在 on_chunk 入口显式处理 EdgeTTS float32 bytes -> int16 bytes 转换
- 通过 WarudoStateManager.mouth_state.set_vowel_state 写入元音强度
- 100ms 监控循环(由 WarudoStateManager 内部)自动将口型发送到 Warudo WebSocket
"""

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Union

from src.modules.logging import get_logger
from src.stages.output.handlers.avatar.vts.lip_sync_processor import LipSyncProcessor

if TYPE_CHECKING:
    from src.modules.streaming.audio_chunk import AudioChunk, AudioMetadata
    from src.stages.output.handlers.avatar.warudo.state.warudo_state_manager import (
        WarudoStateManager,
    )


HookCallback = Optional[Callable[[], Union[Awaitable[Any], Any]]]


class WarudoLipSyncSubscriber:
    """Warudo 音频口型同步订阅器

    包装 VTS 的 LipSyncProcessor,适配 WarudoStateManager。
    提供 on_audio_start/on_audio_end 钩子,让外部可联动状态(如 start_talking)。
    """

    def __init__(
        self,
        *,
        state_manager: "WarudoStateManager",
        logger_name: str = "WarudoLipSync",
        sample_rate: int = 16000,
        volume_threshold: float = 0.01,
        smoothing_factor: float = 0.3,
        vowel_detection_sensitivity: float = 0.5,
        is_connected: Optional[Callable[[], bool]] = None,
        on_audio_start_hook: HookCallback = None,
        on_audio_end_hook: HookCallback = None,
    ):
        self.state_manager = state_manager
        self.logger = get_logger(logger_name)
        self._is_connected = is_connected or (lambda: True)
        self._on_audio_start_hook = on_audio_start_hook
        self._on_audio_end_hook = on_audio_end_hook

        self.lip_sync = LipSyncProcessor(
            logger_name=f"{logger_name}.LipSync",
            sample_rate=sample_rate,
            volume_threshold=volume_threshold,
            smoothing_factor=smoothing_factor,
            vowel_detection_sensitivity=vowel_detection_sensitivity,
            vts_set_parameter=self._warudo_set_vowel,
            is_connected=self._is_connected,
        )

    async def _warudo_set_vowel(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        """将 VTS 风格回调转换为 Warudo MouthState 写入

        VTS LipSyncProcessor 会发送 MouthOpen 等参数,但 Warudo 端只需要
        VowelA/I/U/E/O 五元音强度。我们直接从 lip_sync 内部的
        current_vowel_values 读取并写入 Warudo MouthState。

        Args:
            parameter_name: 参数名(此回调中忽略,只用于协议兼容)
            value: 参数值(忽略)
            weight: 权重(忽略)

        Returns:
            是否成功
        """
        try:
            vowel_values = self.lip_sync.current_vowel_values
            if vowel_values:
                vowel_states = {
                    "VowelA": float(vowel_values.get("A", 0.0)),
                    "VowelI": float(vowel_values.get("I", 0.0)),
                    "VowelU": float(vowel_values.get("U", 0.0)),
                    "VowelE": float(vowel_values.get("E", 0.0)),
                    "VowelO": float(vowel_values.get("O", 0.0)),
                }
                self.state_manager.mouth_state.set_vowel_state(vowel_states)
            return True
        except Exception as e:
            self.logger.error(f"写入口型状态失败: {e}")
            return False

    async def on_start(self, metadata: "AudioMetadata") -> None:
        """AudioStreamChannel: 音频流开始回调"""
        await self.lip_sync.on_start(metadata)
        if self._on_audio_start_hook is not None:
            try:
                result = self._on_audio_start_hook()
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                self.logger.error(f"on_audio_start_hook 回调失败: {e}")

    async def on_chunk(self, chunk: "AudioChunk") -> None:
        """AudioStreamChannel: 音频块回调

        显式处理 EdgeTTS 发布 float32 bytes 的情况:
        AudioChunk.data 文档说 int16,但 EdgeTTS 实际发布 float32.tobytes()
        必须在 resample_audio 之前转换,否则 resample_audio 内部
        np.frombuffer(..., dtype=np.int16) 会得到错误数据。
        """
        if not self._is_connected():
            return
        try:
            import numpy as np

            from src.modules.streaming.audio_utils import resample_audio

            audio_bytes = chunk.data
            audio_array = np.frombuffer(audio_bytes, dtype=np.float32)

            if len(audio_array) > 0 and np.max(np.abs(audio_array)) > 1.0:
                pass
            else:
                audio_array = (audio_array * 32767).astype(np.int16)
                audio_bytes = audio_array.tobytes()

            audio_data = resample_audio(audio_bytes, chunk.sample_rate, self.lip_sync._sample_rate)
            await self.lip_sync.process_audio(audio_data, self.lip_sync._sample_rate)
        except Exception as e:
            self.logger.error(f"处理音频块失败: {e}")

    async def on_end(self, metadata: "AudioMetadata") -> None:
        """AudioStreamChannel: 音频流结束回调"""
        await self.lip_sync.on_end(metadata)
        if self._on_audio_end_hook is not None:
            try:
                result = self._on_audio_end_hook()
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                self.logger.error(f"on_audio_end_hook 回调失败: {e}")
