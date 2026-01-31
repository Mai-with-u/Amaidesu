"""
TTS Output Provider

使用Edge TTS或Omni TTS进行语音合成并播放。
"""

import asyncio
import os
import tempfile
from typing import Optional

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    sd = None
    sf = None

from src.core.providers.output_provider import OutputProvider
from src.core.providers.base import RenderParameters
from src.utils.logger import get_logger


class TTSOutputProvider(OutputProvider):
    """
    TTS Output Provider

    负责TTS语音合成和播放。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

        # 依赖检查
        if edge_tts is None:
            self.logger.error("edge_tts library not found. Please install it (`pip install edge-tts`).")
            raise ImportError("edge_tts is required for TTSOutputProvider")
        if sd is None or sf is None:
            self.logger.error("sounddevice or soundfile library not found.")
            raise ImportError("sounddevice and soundfile are required for TTSOutputProvider")

        # 配置
        self.voice = self.config.get("voice", "zh-CN-XiaoxiaoNeural")
        self.output_device_name = self.config.get("output_device_name") or None
        self.output_device_index = self._find_device_index(self.output_device_name)

        # Omni TTS配置
        self.omni_enabled = self.config.get("omni_tts", {}).get("enable", False)
        self.omni_tts = None

        # 音频播放锁
        self.tts_lock = asyncio.Lock()

    def _find_device_index(self, device_name: Optional[str], kind: str = "output") -> Optional[int]:
        """查找音频设备索引"""
        if sd is None:
            self.logger.error("sounddevice library not available")
            return None

        try:
            devices = sd.query_devices()
            if device_name:
                for i, device in enumerate(devices):
                    device_name_attr = getattr(device, "name", "")
                    channels_attr = getattr(device, f"max_{kind}_channels", 0)
                    if device_name.lower() in device_name_attr.lower() and channels_attr > 0:
                        self.logger.info(f"Found output device '{device_name_attr}' (index: {i})")
                        return i

            default_device_indices = sd.default.device
            default_index = default_device_indices[1] if kind == "output" else default_device_indices[0]
            if default_index == -1:
                return None

            return default_index
        except Exception as e:
            self.logger.error(f"Error finding audio device: {e}", exc_info=True)
            return None

    async def _setup_internal(self):
        """设置TTS Provider"""
        self.logger.info("Setting up TTSOutputProvider...")

        # 初始化Omni TTS（如果启用）
        if self.omni_enabled:
            try:
                from src.plugins.omni_tts.omni_tts import OmniTTS

                self.omni_tts = OmniTTS(self.config.get("omni_tts", {}))
                self.logger.info("Omni TTS enabled")
            except Exception as e:
                self.logger.error(f"Failed to initialize Omni TTS: {e}", exc_info=True)
                self.omni_enabled = False

    async def _render_internal(self, parameters: RenderParameters):
        """
        渲染参数

        根据render_type处理：
        - "tts": 文本转语音并播放
        """
        if parameters.render_type != "tts":
            self.logger.warning(f"Unsupported render_type: {parameters.render_type}")
            return

        text = parameters.content
        if not text or not isinstance(text, str):
            self.logger.warning("Empty or invalid text for TTS")
            return

        try:
            await self._speak(text)
        except Exception as e:
            self.logger.error(f"Error during TTS: {e}", exc_info=True)

    async def _speak(self, text: str):
        """TTS合成和播放"""
        self.logger.info(f"TTS: '{text[:50]}...'")

        async with self.tts_lock:
            tmp_filename = None
            try:
                # 使用Edge TTS或Omni TTS合成
                if self.omni_enabled and self.omni_tts:
                    audio_data = await self.omni_tts.generate_audio(text)
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".wav", dir=tempfile.gettempdir()
                    ) as tmp_file:
                        tmp_filename = tmp_file.name
                        tmp_file.write(audio_data)
                    self.logger.debug(f"Omni TTS audio saved to: {tmp_filename}")
                else:
                    communicate = edge_tts.Communicate(text, self.voice)
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".mp3", dir=tempfile.gettempdir()
                    ) as tmp_file:
                        tmp_filename = tmp_file.name
                    await asyncio.to_thread(communicate.save_sync, tmp_filename)
                    self.logger.debug(f"Edge TTS audio saved to: {tmp_filename}")

                # 读取音频
                audio_array, samplerate = await asyncio.to_thread(sf.read, tmp_filename, dtype="float32")

                # 播放音频
                self.logger.info(f"Playing TTS audio (duration: {len(audio_array) / samplerate:.2f}s)")
                sd.play(audio_array, samplerate=samplerate, device=self.output_device_index, blocking=True)

            finally:
                if tmp_filename and os.path.exists(tmp_filename):
                    try:
                        os.remove(tmp_filename)
                        self.logger.debug(f"Deleted temp file: {tmp_filename}")
                    except Exception as e:
                        self.logger.warning(f"Error deleting temp file: {e}")

    async def _cleanup_internal(self):
        """清理TTS Provider"""
        self.logger.info("Cleaning up TTSOutputProvider...")
        # 停止所有播放
        if sd:
            try:
                sd.stop()
            except Exception as e:
                self.logger.debug(f"Error stopping playback: {e}")
