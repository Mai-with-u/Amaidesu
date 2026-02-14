"""
EdgeTTS Provider - Output Domain: 渲染输出实现

职责:
- 使用Edge TTS引擎进行文本转语音并播放
"""

import asyncio
import tempfile
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.tts import AudioDeviceManager
from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.modules.di.context import ProviderContext
    from src.modules.types import Intent

# 导入工具函数
from .utils.device_finder import find_device_index

# 检查依赖
DEPENDENCIES_OK = True
try:
    import soundfile as sf
except ImportError:
    DEPENDENCIES_OK = False

try:
    import edge_tts
except ImportError:
    pass  # edge_tts可选，由配置决定使用哪个引擎


class EdgeTTSProvider(OutputProvider):
    """
    EdgeTTS Provider实现

    核心功能:
    - 使用Edge TTS引擎进行文本转语音
    - 音频播放和设备管理
    """

    class ConfigSchema(BaseProviderConfig):
        """EdgeTTS输出Provider配置"""

        type: str = "edge_tts"

        # Edge TTS配置
        voice: str = Field(default="zh-CN-XiaoxiaoNeural", description="Edge TTS语音")
        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")

    def __init__(self, config: Dict[str, Any], context: "ProviderContext"):
        """
        初始化EdgeTTS Provider

        Args:
            config: Provider配置（来自[providers.output.edge_tts]）
        """
        super().__init__(config, context)
        self.logger = get_logger("EdgeTTSProvider")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # Edge TTS配置
        self.voice = self.typed_config.voice
        self.output_device_name = self.typed_config.output_device_name or ""

        # 音频设备管理器（在 init 中初始化）
        self.audio_manager: Optional[AudioDeviceManager] = None

        # 音频播放配置
        self.tts_lock = asyncio.Lock()

        self.logger.info(f"EdgeTTSProvider初始化完成，语音: {self.voice}")

    async def init(self):
        """初始化 Provider"""
        # 验证依赖
        if not DEPENDENCIES_OK:
            raise RuntimeError("缺少必要的依赖: soundfile")

        if "edge_tts" not in globals():
            raise RuntimeError("Edge TTS引擎未安装")

        # 初始化音频设备管理器
        sample_rate = 48000  # Edge TTS 默认采样率
        channels = 1
        dtype = np.float32  # soundfile 读取的是 float32

        self.audio_manager = AudioDeviceManager(sample_rate=sample_rate, channels=channels, dtype=dtype)

        # AudioStreamChannel 已由基类设置，通过属性访问

        # 设置输出设备
        if self.output_device_name:
            device_index = find_device_index(
                device_name=self.output_device_name,
                logger=self.logger,
                sample_rate=sample_rate,
                channels=channels,
                dtype=dtype,
            )
            if device_index is not None:
                self.audio_manager.set_output_device(device_index=device_index)

        self.logger.info("EdgeTTSProvider启动完成")

    async def execute(self, intent: "Intent"):
        """
        执行 TTS 输出

        Args:
            intent: Intent对象
        """
        text = intent.response_text
        if not text:
            self.logger.debug("TTS文本为空，跳过渲染")
            return

        self.logger.info(f"开始TTS渲染: '{text[:30]}...'")
        await self._speak(text)

    async def _speak(self, text: str):
        """
        执行TTS合成和播放

        Args:
            text: 要合成的文本
        """
        async with self.tts_lock:
            # 通知订阅者: 音频开始
            if self.audio_stream_channel:
                from src.modules.streaming.audio_chunk import AudioMetadata

                await self.audio_stream_channel.notify_start(AudioMetadata(text=text, sample_rate=48000, channels=1))

            tmp_filename = None
            try:
                # 合成语音
                audio_array, samplerate = await self._edge_tts_synthesize(text)

                # 计算音频时长
                duration_seconds = len(audio_array) / samplerate if samplerate > 0 else 3.0
                self.logger.info(f"音频时长: {duration_seconds:.3f}秒")

                # 发布音频块
                chunk_size = 1024
                for i in range(0, len(audio_array), chunk_size):
                    chunk_data = audio_array[i : i + chunk_size]
                    if self.audio_stream_channel:
                        from src.modules.streaming.audio_chunk import AudioChunk

                        chunk = AudioChunk(
                            data=chunk_data.tobytes(),
                            sample_rate=samplerate,
                            channels=1,
                            sequence=i // chunk_size,
                            timestamp=time.time(),
                        )
                        await self.audio_stream_channel.publish(chunk)

                # 播放音频
                self.logger.info("开始播放音频...")
                await self.audio_manager.play_audio(audio_array)

            except Exception as e:
                self.logger.error(f"TTS渲染失败: {e}", exc_info=True)
                raise RuntimeError(f"TTS渲染失败: {e}") from e

            finally:
                # 通知订阅者: 音频结束
                if self.audio_stream_channel:
                    from src.modules.streaming.audio_chunk import AudioMetadata

                    await self.audio_stream_channel.notify_end(AudioMetadata(text=text, sample_rate=48000, channels=1))

                # 清理临时文件
                if tmp_filename and tmp_filename.startswith(tempfile.gettempdir()):
                    try:
                        import os

                        os.remove(tmp_filename)
                        self.logger.debug(f"已删除临时文件: {tmp_filename}")
                    except Exception as e_rem:
                        self.logger.warning(f"删除临时文件失败: {e_rem}")

    async def _edge_tts_synthesize(self, text: str):
        """
        使用Edge TTS合成语音

        Args:
            text: 要合成的文本

        Returns:
            audio_array, samplerate
        """
        if "edge_tts" not in globals():
            raise RuntimeError("Edge TTS未安装")

        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                tmp_filename = tmp_file.name

            # 合成语音
            communicate = edge_tts.Communicate(text, self.voice)
            await asyncio.to_thread(communicate.save_sync, tmp_filename)
            self.logger.info(f"Edge TTS合成完成: {tmp_filename}")

            # 读取音频
            audio_array, samplerate = await asyncio.to_thread(sf.read, tmp_filename, dtype="float32")

            return audio_array, samplerate

        except Exception as e:
            self.logger.error(f"Edge TTS合成失败: {e}", exc_info=True)
            # 返回静音作为降级方案
            return np.zeros(44100, dtype=np.float32), 16000

    async def cleanup(self):
        """清理资源"""
        self.logger.info("EdgeTTSProvider停止中...")

        # 停止所有播放
        if self.audio_manager:
            self.audio_manager.stop_audio()

        self.logger.info("EdgeTTSProvider停止完成")
