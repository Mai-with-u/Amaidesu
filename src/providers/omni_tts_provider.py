"""
OmniTTS Provider - Layer 6 Rendering层实现

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
from typing import Dict, Any, Optional
from collections import deque

import numpy as np

from src.core.providers.output_provider import OutputProvider
from src.expression.render_parameters import RenderParameters
from src.utils.logger import get_logger

# 检查依赖
TTS_DEPENDENCIES_OK = False
try:
    import requests
    import sounddevice as sd
    import soundfile as sf  # noqa: F401

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

    def __init__(self, config: Dict[str, Any]):
        """
        初始化OmniTTS Provider

        Args:
            config: Provider配置（来自[rendering.outputs.omni_tts]）
        """
        super().__init__(config)
        self.logger = get_logger("OmniTTSProvider")

        # GPT-SoVITS API配置
        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", 9880)
        self.base_url = f"http://{self.host}:{self.port}"

        # 参考音频配置
        self.ref_audio_path = config.get("ref_audio_path", "")
        self.prompt_text = config.get("prompt_text", "")

        # TTS参数
        self.text_language = config.get("text_language", "zh")
        self.prompt_language = config.get("prompt_language", "zh")
        self.top_k = config.get("top_k", 20)
        self.top_p = config.get("top_p", 0.6)
        self.temperature = config.get("temperature", 0.3)
        self.speed_factor = config.get("speed_factor", 1.0)
        self.streaming_mode = config.get("streaming_mode", True)

        # 音频输出配置
        self.output_device_name = config.get("output_device_name", "")
        self.output_device_index = None
        self.sample_rate = config.get("sample_rate", 32000)
        self.channels = 1
        self.dtype = np.int16

        # 服务集成配置
        self.use_text_cleanup = config.get("use_text_cleanup", True)
        self.use_vts_lip_sync = config.get("use_vts_lip_sync", True)
        self.use_subtitle = config.get("use_subtitle", True)

        # TTS锁
        self.tts_lock = asyncio.Lock()

        # 音频缓冲区
        self.input_pcm_queue = deque(b"")
        self.audio_data_queue = deque(maxlen=1000)
        self.input_pcm_queue_lock = asyncio.Lock()

        # 统计信息
        self.render_count = 0
        self.error_count = 0

        self.logger.info("OmniTTSProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
        if not TTS_DEPENDENCIES_OK:
            self.logger.error("TTS依赖缺失，请安装: pip install requests sounddevice soundfile")
            raise ImportError("TTS dependencies not available")

        # 查找音频设备
        self.output_device_index = self._find_device_index(self.output_device_name)

        if self.output_device_index is None:
            self.logger.warning(f"未找到音频设备 '{self.output_device_name}'，使用默认设备")
        else:
            self.logger.info(f"使用音频设备索引: {self.output_device_index}")

        # 初始化音频流
        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype=self.dtype,
            device=self.output_device_index,
            callback=self._audio_callback,
            blocksize=1024,
        )

        self.logger.info("OmniTTSProvider设置完成")

    def _find_device_index(self, device_name: Optional[str]) -> Optional[int]:
        """查找音频设备索引"""
        if not device_name:
            return None

        try:
            devices = sd.query_devices()
            for i, device in enumerate(devices):
                if device_name.lower() in device["name"].lower() and device["max_output_channels"] > 0:
                    self.logger.info(f"找到音频设备 '{device['name']}'，索引: {i}")
                    return i
            self.logger.warning(f"未找到音频设备 '{device_name}'")
            return None
        except Exception as e:
            self.logger.error(f"查找音频设备失败: {e}")
            return None

    def _audio_callback(self, outdata, frames, time_info, status):
        """音频播放回调"""
        try:
            if len(self.audio_data_queue) > 0:
                data = self.audio_data_queue.popleft()
                outdata[:] = np.frombuffer(data, dtype=self.dtype).reshape(frames, self.channels)
            else:
                outdata[:] = 0  # 静音
        except Exception as e:
            self.logger.error(f"音频回调失败: {e}")

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
            # 启动音频流
            if self.stream and not self.stream.active:
                self.stream.start()

            # 执行TTS
            await self._speak(text)

            self.logger.info(f"TTS渲染完成: '{text[:30]}...'")

        except Exception as e:
            self.logger.error(f"OmniTTS渲染失败: {e}", exc_info=True)
            raise RuntimeError(f"OmniTTS渲染失败: {e}") from e

    async def _speak(self, text: str):
        """执行TTS并播放"""
        async with self.tts_lock:
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
            pcm_data = self._extract_pcm_from_wav(wav_data)

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
                self.audio_data_queue.append(raw_block)

        except Exception as e:
            self.logger.error(f"解码音频数据失败: {e}")

    def _extract_pcm_from_wav(self, wav_data: bytes) -> bytes:
        """从WAV数据中提取PCM数据"""
        try:
            # WAV header is at least 44 bytes
            if len(wav_data) < 44:
                return wav_data

            # Find "data" chunk
            data_pos = wav_data.find(b"data")
            if data_pos == -1:
                return wav_data[44:]  # Skip standard header

            # Skip "data" marker and size (4 + 4 = 8 bytes)
            pcm_start = data_pos + 8
            return wav_data[pcm_start:]

        except Exception as e:
            self.logger.error(f"提取PCM数据失败: {e}")
            return wav_data

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("OmniTTSProvider清理中...")

        # 停止音频流
        if self.stream and self.stream.active:
            self.stream.stop()
            self.stream.close()

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
            "stream_active": self.stream.active if self.stream else False,
            "buffer_size": len(self.input_pcm_queue),
        }
