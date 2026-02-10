"""
OmniTTS Provider - Output Domain: 渲染输出实现

职责:
- 使用GPT-SoVITS引擎进行文本转语音
- 支持流式TTS和音频播放
- 集成text_cleanup、vts_lip_sync、subtitle_service等服务
- 向后兼容现有TTS插件功能

注意: 这是一个简化版本的Provider,专注于核心TTS功能
如果需要完整功能(如远程流、UDP广播等),请继续使用原有的TTS插件
"""

import asyncio
import base64
import time
from collections import deque
from typing import Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.domains.output.parameters.render_parameters import RenderParameters
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.tts import AudioDeviceManager
from src.modules.types.base.output_provider import OutputProvider

# 导入工具函数
from .utils.device_finder import find_device_index
from .utils.wav_decoder import extract_pcm_from_wav

# 检查依赖
TTS_DEPENDENCIES_OK = False
try:
    import requests

    TTS_DEPENDENCIES_OK = True
except ImportError:
    pass


class OmniTTSProvider(OutputProvider):
    """
    OmniTTS Provider实现

    核心功能:
    - 使用GPT-SoVITS API进行文本转语音
    - 流式TTS和音频播放
    - 集成text_cleanup、vts_lip_sync、subtitle_service等服务
    """

    class ConfigSchema(BaseProviderConfig):
        """Omni TTS输出Provider配置"""

        type: str = "omni_tts"

        # GPT-SoVITS API配置
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

        # 音频输出配置
        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")
        sample_rate: int = Field(default=32000, ge=8000, le=48000, description="采样率")

        # 服务集成配置
        use_text_cleanup: bool = Field(default=True, description="是否使用文本清理")
        use_vts_lip_sync: bool = Field(default=True, description="是否使用VTS口型同步")
        use_subtitle: bool = Field(default=True, description="是否使用字幕")

    def __init__(self, config: Dict[str, Any]):
        """
        初始化OmniTTS Provider

        Args:
            config: Provider配置（来自[rendering.outputs.omni_tts]）
        """
        super().__init__(config)
        self.logger = get_logger("OmniTTSProvider")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # GPT-SoVITS API配置
        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.base_url = f"http://{self.host}:{self.port}"

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

        # 音频输出配置
        self.output_device_name = self.typed_config.output_device_name
        self.sample_rate = self.typed_config.sample_rate
        self.channels = 1
        self.dtype = np.int16

        # 音频设备管理器（在_setup_internal中初始化）
        self.audio_manager: Optional[AudioDeviceManager] = None

        # 服务集成配置
        self.use_text_cleanup = self.typed_config.use_text_cleanup
        self.use_vts_lip_sync = self.typed_config.use_vts_lip_sync
        self.use_subtitle = self.typed_config.use_subtitle

        # TTS锁
        self.tts_lock = asyncio.Lock()

        # 音频缓冲区
        self.input_pcm_queue = deque(b"")
        self.audio_data_queue = deque(maxlen=1000)
        self.input_pcm_queue_lock = asyncio.Lock()

        # 统计信息
        self.render_count = 0
        self.error_count = 0

        # 音频序列计数器（用于 AudioStreamChannel）
        self.sequence_count = 0

        self.logger.info("OmniTTSProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
        if not TTS_DEPENDENCIES_OK:
            self.logger.error("TTS依赖缺失，请安装: pip install requests")
            raise ImportError("TTS dependencies not available")

        # 初始化音频设备管理器
        self.audio_manager = AudioDeviceManager(sample_rate=self.sample_rate, channels=self.channels, dtype=self.dtype)

        # 设置输出设备
        if self.output_device_name:
            device_index = find_device_index(
                device_name=self.output_device_name,
                logger=self.logger,
                sample_rate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
            )
            if device_index is not None:
                self.audio_manager.set_output_device(device_index=device_index)

        # 从 dependencies 获取 AudioStreamChannel
        self.audio_stream_channel = self._dependencies.get("audio_stream_channel")

        self.logger.info("OmniTTSProvider设置完成")

    async def _render_internal(self, parameters: RenderParameters):
        """
        渲染TTS输出

        Args:
            parameters: RenderParameters对象
        """
        if not parameters.tts_enabled or not parameters.tts_text:
            self.logger.debug("TTS未启用或文本为空，跳过渲染")
            return

        text = parameters.tts_text

        try:
            # 执行TTS
            await self._speak(text)

            self.logger.info(f"TTS渲染完成: '{text[:30]}...'")

        except Exception as e:
            self.logger.error(f"OmniTTS渲染失败: {e}", exc_info=True)
            raise RuntimeError(f"OmniTTS渲染失败: {e}") from e

    async def _speak(self, text: str):
        """执行TTS并播放"""
        async with self.tts_lock:
            # 通知订阅者: 音频开始
            if self.audio_stream_channel:
                from src.modules.streaming.audio_chunk import AudioMetadata

                await self.audio_stream_channel.notify_start(
                    AudioMetadata(text=text, sample_rate=self.sample_rate, channels=self.channels)
                )

            # 重置序列计数器
            self.sequence_count = 0

            try:
                # 发起流式TTS请求
                audio_stream = self._tts_stream(text)

                # 处理音频流
                for chunk in audio_stream:
                    if not chunk:
                        continue

                    # 解码并缓冲音频
                    await self._decode_and_buffer(chunk)

            except Exception as e:
                self.logger.error(f"TTS播放失败: {e}")
                raise

            finally:
                # 通知订阅者: 音频结束
                if self.audio_stream_channel:
                    from src.modules.streaming.audio_chunk import AudioMetadata

                    await self.audio_stream_channel.notify_end(
                        AudioMetadata(text=text, sample_rate=self.sample_rate, channels=self.channels)
                    )

    def _tts_stream(self, text: str):
        """发起流式TTS请求"""
        if not self.ref_audio_path:
            raise ValueError("未设置参考音频")

        params = {
            "text": text,
            "text_lang": self.text_language,
            "ref_audio_path": self.ref_audio_path,
            "prompt_text": self.prompt_text,
            "prompt_lang": self.prompt_language,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "speed_factor": self.speed_factor,
            "streaming_mode": True,
            "media_type": "wav",
        }

        response = requests.get(
            f"{self.base_url}/tts",
            params=params,
            stream=True,
            timeout=(3.05, None),
            headers={"Connection": "keep-alive"},
        )

        if response.status_code != 200:
            error_msg = (
                response.json().get("message", "未知错误")
                if response.headers.get("content-type", "").startswith("application/json")
                else "未知错误"
            )
            raise Exception(f"TTS API错误: {error_msg}")

        return response.iter_content(chunk_size=4096)

    async def _decode_and_buffer(self, wav_chunk):
        """解码WAV数据并缓冲"""
        try:
            # 解析WAV数据
            if isinstance(wav_chunk, str):
                wav_data = base64.b64decode(wav_chunk)
            else:
                wav_data = wav_chunk

            # 提取PCM数据
            pcm_data = extract_pcm_from_wav(wav_data)

            # 缓冲PCM数据
            async with self.input_pcm_queue_lock:
                self.input_pcm_queue.extend(pcm_data)

            # 切割音频块
            block_size = 1024 * self.channels * self.dtype().itemsize
            while len(self.input_pcm_queue) >= block_size:
                raw_block = b""
                async with self.input_pcm_queue_lock:
                    for _ in range(block_size):
                        raw_block += bytes([self.input_pcm_queue.popleft()])

                # 发布音频块
                if self.audio_stream_channel:
                    from src.modules.streaming.audio_chunk import AudioChunk

                    chunk = AudioChunk(
                        data=raw_block,
                        sample_rate=self.sample_rate,
                        channels=self.channels,
                        sequence=self.sequence_count,
                        timestamp=time.time(),
                    )
                    await self.audio_stream_channel.publish(chunk)
                    self.sequence_count += 1

                self.audio_data_queue.append(raw_block)

        except Exception as e:
            self.logger.error(f"解码音频数据失败: {e}")

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("OmniTTSProvider清理中...")

        # 停止音频播放
        if self.audio_manager:
            self.audio_manager.stop_audio()

        # 清空缓冲区
        self.input_pcm_queue.clear()
        self.audio_data_queue.clear()

        self.logger.info("OmniTTSProvider清理完成")

    def get_stats(self) -> Dict[str, Any]:
        """获取Provider统计信息"""
        return {
            "name": self.__class__.__name__,
            "is_connected": True,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "buffer_size": len(self.input_pcm_queue),
        }
