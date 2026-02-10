"""
EdgeTTS Provider - Output Domain: 渲染输出实现

职责:
- 使用Edge TTS引擎进行文本转语音并播放
"""

import asyncio
import tempfile
import time
from typing import Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.domains.output.parameters.render_parameters import ExpressionParameters
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.tts import AudioDeviceManager
from src.modules.types.base.output_provider import OutputProvider

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

    def __init__(self, config: Dict[str, Any]):
        """
        初始化EdgeTTS Provider

        Args:
            config: Provider配置（来自[providers.output.edge_tts]）
        """
        super().__init__(config)
        self.logger = get_logger("EdgeTTSProvider")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # Edge TTS配置
        self.voice = self.typed_config.voice
        self.output_device_name = self.typed_config.output_device_name or ""

        # 音频设备管理器（在_setup_internal中初始化）
        self.audio_manager: Optional[AudioDeviceManager] = None

        # 音频播放配置
        self.tts_lock = asyncio.Lock()

        self.logger.info(f"EdgeTTSProvider初始化完成，语音: {self.voice}")

    async def _setup_internal(self):
        """内部设置逻辑"""
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

        # 从 dependencies 获取 AudioStreamChannel
        self.audio_stream_channel = self._dependencies.get("audio_stream_channel")

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

        # 订阅 expression.parameters_generated 事件（事件驱动架构）
        if self.event_bus:
            self.event_bus.on(CoreEvents.EXPRESSION_PARAMETERS_GENERATED, self._on_parameters_ready, priority=50)
            self.logger.info("EdgeTTSProvider 已订阅 expression.parameters_generated 事件")

        self.logger.info("EdgeTTSProvider设置完成")

    async def _render_internal(self, parameters: ExpressionParameters):
        """
        渲染TTS输出

        Args:
            parameters: ExpressionParameters对象
        """
        if not parameters.tts_enabled or not parameters.tts_text:
            self.logger.debug("TTS未启用或文本为空，跳过渲染")
            return

        text = parameters.tts_text
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

    async def _on_parameters_ready(self, event_name: str, event_data: ExpressionParameters, source: str):
        """
        处理 expression.parameters_generated 事件（事件驱动架构）

        Args:
            event_name: 事件名称
            event_data: ExpressionParameters 对象
            source: 事件源
        """
        # 检查是否启用 TTS
        if not event_data.tts_enabled or not event_data.tts_text:
            return

        try:
            await self._render_internal(event_data)
        except Exception as e:
            self.logger.error(f"TTS 渲染失败: {e}", exc_info=True)

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("EdgeTTSProvider清理中...")

        # 取消事件订阅
        if self.event_bus:
            try:
                self.event_bus.off(CoreEvents.EXPRESSION_PARAMETERS_GENERATED, self._on_parameters_ready)
                self.logger.debug("EdgeTTSProvider 已取消事件订阅")
            except Exception as e:
                self.logger.warning(f"取消事件订阅失败: {e}")

        # 停止所有播放
        if self.audio_manager:
            self.audio_manager.stop_audio()

        self.logger.info("EdgeTTSProvider清理完成")
