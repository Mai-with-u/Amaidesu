"""
GPTSoVITS OutputProvider - Output Domain: 渲染输出实现

职责:
- 使用GPT-SoVITS引擎进行文本转语音
- 流式TTS和音频播放
- 参考音频管理
- 音频设备管理
"""

import asyncio
import time
from collections import deque
from typing import Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.types.intent import Intent
from src.modules.logging import get_logger
from src.modules.tts import AudioDeviceManager, GPTSoVITSClient
from src.modules.types.base.output_provider import OutputProvider

# 导入工具函数
from .utils.wav_decoder import decode_wav_chunk

# --- 音频流参数 ---
CHANNELS = 1
DTYPE = np.int16
BLOCKSIZE = 1024
SAMPLE_SIZE = DTYPE().itemsize
BUFFER_REQUIRED_BYTES = BLOCKSIZE * CHANNELS * SAMPLE_SIZE


class GPTSoVITSOutputProvider(OutputProvider):
    """
    GPTSoVITS OutputProvider实现

    核心功能:
    - 使用GPT-SoVITS API进行文本转语音
    - 流式TTS和音频播放
    - 音频设备管理
    """

    class ConfigSchema(BaseProviderConfig):
        """GPT-SoVITS输出Provider配置"""

        type: str = "gptsovits"

        # API配置
        host: str = Field(default="127.0.0.1", description="API主机地址")
        port: int = Field(default=9880, ge=1, le=65535, description="API端口")

        # 参考音频配置
        ref_audio_path: str = Field(default="", description="参考音频路径")
        prompt_text: str = Field(default="", description="提示文本")

        # TTS参数
        text_language: str = Field(default="zh", pattern=r"^(zh|en|ja|auto)$", description="文本语言")
        prompt_language: str = Field(default="zh", pattern=r"^(zh|en|ja)$", description="提示语言")
        top_k: int = Field(default=20, ge=1, le=100, description="Top-K采样")
        top_p: float = Field(default=0.6, ge=0.0, le=1.0, description="Top-P采样")
        temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="温度参数")
        speed_factor: float = Field(default=1.0, ge=0.1, le=3.0, description="语速因子")
        streaming_mode: bool = Field(default=True, description="是否启用流式模式")
        media_type: str = Field(default="wav", pattern=r"^(wav|mp3|ogg)$", description="媒体类型")
        text_split_method: str = Field(
            default="latency", pattern=r"^(latency|punctuation)$", description="文本分割方法"
        )
        batch_size: int = Field(default=1, ge=1, le=10, description="批处理大小")
        batch_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="批处理阈值")
        repetition_penalty: float = Field(default=1.0, ge=0.5, le=2.0, description="重复惩罚")
        sample_steps: int = Field(default=10, ge=1, le=50, description="采样步数")
        super_sampling: bool = Field(default=True, description="是否启用超采样")

        # 音频输出配置
        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")
        sample_rate: int = Field(default=32000, ge=8000, le=48000, description="采样率")

    def __init__(self, config: Dict[str, Any]):
        """
        初始化GPTSoVITS OutputProvider

        Args:
            config: Provider配置（来自[providers.output.gptsovits]）
        """
        super().__init__(config)
        self.logger = get_logger("GPTSoVITSOutputProvider")

        # 使用 ConfigSchema 验证配置
        self.typed_config = self.ConfigSchema(**config)

        # GPT-SoVITS API配置
        self.host = self.typed_config.host
        self.port = self.typed_config.port

        # 参考音频配置
        self.ref_audio_path = self.typed_config.ref_audio_path
        self.prompt_text = self.typed_config.prompt_text

        # TTS参数
        self.text_language = self.typed_config.text_language
        self.prompt_language = self.typed_config.prompt_language
        self.top_k = self.typed_config.top_k
        self.top_p = self.typed_config.top_p
        self.temperature = self.typed_config.temperature
        self.speed_factor = self.typed_config.speed_factor
        self.streaming_mode = self.typed_config.streaming_mode
        self.media_type = self.typed_config.media_type
        self.text_split_method = self.typed_config.text_split_method
        self.batch_size = self.typed_config.batch_size
        self.batch_threshold = self.typed_config.batch_threshold
        self.repetition_penalty = self.typed_config.repetition_penalty
        self.sample_steps = self.typed_config.sample_steps
        self.super_sampling = self.typed_config.super_sampling

        # 音频输出配置
        self.sample_rate = self.typed_config.sample_rate

        # TTS锁
        self.tts_lock = asyncio.Lock()

        # 音频缓冲区
        self.input_pcm_queue = deque(b"")
        self.audio_data_queue = deque(maxlen=1000)
        self.input_pcm_queue_lock = asyncio.Lock()

        # 统计信息
        self.render_count = 0
        self.error_count = 0

        # 客户端和设备管理器（在 init 中初始化）
        self.tts_client: Optional[GPTSoVITSClient] = None
        self.audio_manager: Optional[AudioDeviceManager] = None

        self.logger.info("GPTSoVITSOutputProvider初始化完成")

    async def init(self):
        """初始化逻辑"""
        # 初始化TTS客户端
        self.tts_client = GPTSoVITSClient(self.host, self.port)
        self.tts_client.initialize()

        # 设置参考音频
        if self.ref_audio_path and self.prompt_text:
            self.tts_client.set_refer_audio(self.ref_audio_path, self.prompt_text)

        # 初始化音频设备管理器
        self.audio_manager = AudioDeviceManager(sample_rate=self.sample_rate, channels=CHANNELS, dtype=DTYPE)

        # 设置输出设备
        if self.typed_config.output_device_name:
            self.audio_manager.set_output_device(device_name=self.typed_config.output_device_name)

        # 加载默认预设
        self.tts_client.load_preset("default")

        # AudioStreamChannel 已由基类设置，通过属性访问
        self.logger.info("GPTSoVITSOutputProvider设置完成")

    async def cleanup(self):
        """清理资源"""
        self.logger.info("GPTSoVITSOutputProvider清理中...")

        # 停止音频播放
        if self.audio_manager:
            self.audio_manager.stop_audio()

        # 清空缓冲区
        async with self.input_pcm_queue_lock:
            self.input_pcm_queue.clear()
        self.audio_data_queue.clear()

        self.logger.info("GPTSoVITSOutputProvider清理完成")

    async def execute(self, intent: "Intent"):
        """
        执行 TTS 输出

        Args:
            intent: Intent对象，从 response_text 获取 TTS 文本
        """
        # 从 Intent 获取 TTS 文本
        text = intent.response_text

        if not text or not text.strip():
            self.logger.debug("TTS文本为空，跳过渲染")
            return

        original_text = text.strip()
        self.logger.info(f"准备TTS: '{original_text[:50]}...'")

        final_text = original_text

        try:
            async with self.tts_lock:
                # 通知订阅者: 音频开始
                audio_channel = getattr(self, "audio_stream_channel", None)
                if audio_channel:
                    from src.modules.streaming.audio_chunk import AudioMetadata

                    await audio_channel.notify_start(
                        AudioMetadata(text=final_text, sample_rate=self.sample_rate, channels=CHANNELS)
                    )

                # 执行TTS（流式）
                audio_stream = self.tts_client.tts_stream(
                    text=final_text,
                    text_lang=self.text_language,
                    prompt_lang=self.prompt_language,
                    top_k=self.top_k,
                    top_p=self.top_p,
                    temperature=self.temperature,
                    speed_factor=self.speed_factor,
                    text_split_method=self.text_split_method,
                    batch_size=self.batch_size,
                    batch_threshold=self.batch_threshold,
                    repetition_penalty=self.repetition_penalty,
                    sample_steps=self.sample_steps,
                    super_sampling=self.super_sampling,
                    media_type=self.media_type,
                )

                # 音频处理和播放
                all_audio_chunks = []
                chunk_index = 0
                async for chunk in self._process_audio_stream(audio_stream):
                    if chunk is not None:
                        # 发布音频块
                        if audio_channel:
                            from src.modules.streaming.audio_chunk import AudioChunk

                            audio_chunk = AudioChunk(
                                data=chunk.tobytes(),
                                sample_rate=self.sample_rate,
                                channels=CHANNELS,
                                sequence=chunk_index,
                                timestamp=time.time(),
                            )
                            await audio_channel.publish(audio_chunk)

                        all_audio_chunks.append(chunk)
                        chunk_index += 1

                # 播放所有音频
                if all_audio_chunks:
                    full_audio = np.concatenate(all_audio_chunks)
                    await self.audio_manager.play_audio(full_audio)

                # 通知订阅者: 音频结束
                if audio_channel:
                    from src.modules.streaming.audio_chunk import AudioMetadata

                    await audio_channel.notify_end(
                        AudioMetadata(text=final_text, sample_rate=self.sample_rate, channels=CHANNELS)
                    )

            self.logger.info(f"TTS播放完成: '{final_text[:30]}...'")
            self.render_count += 1

        except Exception as e:
            self.logger.error(f"TTS渲染失败: {e}", exc_info=True)
            self.error_count += 1
            raise

    async def _process_audio_stream(self, audio_stream):
        """
        处理音频流

        Args:
            audio_stream: 音频流迭代器（同步或异步）

        Yields:
            numpy.ndarray: 音频数据块
        """
        try:
            # 检查是否是异步迭代器
            if hasattr(audio_stream, "__aiter__"):
                # 异步迭代器
                async for chunk in audio_stream:
                    if not chunk:
                        self.logger.debug("收到空音频块，跳过")
                        continue

                    # 解析WAV数据
                    audio_chunk = await decode_wav_chunk(chunk, dtype=DTYPE)
                    if audio_chunk is not None:
                        yield audio_chunk
            else:
                # 同步迭代器
                for chunk in audio_stream:
                    if not chunk:
                        self.logger.debug("收到空音频块，跳过")
                        continue

                    # 解析WAV数据
                    audio_chunk = await decode_wav_chunk(chunk, dtype=DTYPE)
                    if audio_chunk is not None:
                        yield audio_chunk

        except Exception as e:
            self.logger.error(f"处理音频流失败: {e}", exc_info=True)
            raise
