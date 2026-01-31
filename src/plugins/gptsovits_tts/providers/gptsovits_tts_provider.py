"""
GPTSoVITS OutputProvider - Layer 6 Rendering层实现

职责:
- 使用GPT-SoVITS引擎进行文本转语音
- 流式TTS和音频播放
- 集成text_cleanup、vts_lip_sync、subtitle_service等服务
- 向后兼容原有TTS插件功能（TTSModel类、音频处理、服务集成）

注意: 这是从原gptsovits_tts/plugin.py提取的Provider实现
"""

import asyncio
import base64
import struct
from typing import Dict, Any, Optional, TYPE_CHECKING
from collections import deque
import re

import numpy as np

from src.core.providers.output_provider import OutputProvider
from src.expression.render_parameters import RenderParameters
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.expression.intent import Intent
    from maim_message import MessageBase

# --- Dependencies Check ---
TTS_DEPENDENCIES_OK = False
try:
    import sounddevice as sd
    import soundfile as sf
    import requests
except ImportError:
    print("依赖缺失: 请运行 'pip install requests sounddevice soundfile' 来使用音频播放功能。", file=sys.stderr)
    pass

# --- 远程流支持 ---
# 远程流插件未安装，将禁用远程流功能
# 如需启用远程流，请安装 remote_stream 插件
REMOTE_STREAM_AVAILABLE = False

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
    - 集成text_cleanup、vts_lip_sync、subtitle_service等服务
    """

    def __init__(self, config: Dict[str, Any], event_bus=None, core=None):
        """
        初始化GPTSoVITS OutputProvider

        Args:
            config: Provider配置（来自[rendering.outputs.gptsovits_tts]）
            event_bus: EventBus实例（可选）
            core: AmaidesuCore实例（可选，用于访问服务）
        """
        super().__init__(config, event_bus)
        self.core = core
        self.logger = get_logger("GPTSoVITSOutputProvider")

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
        self.media_type = config.get("media_type", "wav")
        self.text_split_method = config.get("text_split_method", "latency")
        self.batch_size = config.get("batch_size", 1)
        self.batch_threshold = config.get("batch_threshold", 0.7)
        self.repetition_penalty = config.get("repetition_penalty", 1.0)
        self.sample_steps = config.get("sample_steps", 10)
        self.super_sampling = config.get("super_sampling", True)

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
        self.use_remote_stream = config.get("use_remote_stream", False)
        self.lip_sync_service_name = config.get("lip_sync_service_name", "vts_lip_sync")

        # TTS锁
        self.tts_lock = asyncio.Lock()
        self.message_lock = asyncio.Lock()

        # 音频缓冲区
        self.input_pcm_queue = deque(b"")
        self.audio_data_queue = deque(maxlen=1000)
        self.input_pcm_queue_lock = asyncio.Lock()

        # 统计信息
        self.render_count = 0
        self.error_count = 0

        # TTSModel实例（后续在_setup_internal中初始化）
        self.tts_model = None

        # 服务引用缓存（在_setup_internal中初始化，避免重复调用get_service）
        self._text_cleanup_service = None
        self._vts_lip_sync_service = None
        self._subtitle_service = None

        self.logger.info("GPTSoVITSOutputProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
        if not TTS_DEPENDENCIES_OK:
            self.logger.error("TTS依赖缺失，请安装: pip install requests sounddevice soundfile")
            raise ImportError("TTS dependencies not available")

        # 查找音频设备
        self.output_device_index = self._find_device_index(self.output_device_name)

        # 初始化TTSModel（从原插件提取的TTSModel类）
        self.tts_model = TTSModel(self.host, self.port)

        # 加载默认预设
        self.tts_model.load_preset("default")

        # 一次性获取服务引用（避免在渲染时重复调用get_service）
        if self.core:
            if self.use_text_cleanup:
                self._text_cleanup_service = self.core.get_service("text_cleanup")
                if self._text_cleanup_service:
                    self.logger.info("已获取 text_cleanup 服务引用")

            if self.use_vts_lip_sync:
                self._vts_lip_sync_service = self.core.get_service(self.lip_sync_service_name)
                if self._vts_lip_sync_service:
                    self.logger.info(f"已获取 {self.lip_sync_service_name} 服务引用")

            if self.use_subtitle:
                self._subtitle_service = self.core.get_service("subtitle_service")
                if self._subtitle_service:
                    self.logger.info("已获取 subtitle_service 服务引用")

        self.logger.info("GPTSoVITSOutputProvider设置完成")

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
            self.logger.warning(f"未找到名称包含 '{device_name}' 的音频设备，将使用默认设备")
        except Exception as e:
            self.logger.error(f"查找音频设备失败: {e}")
            return None

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        if self.tts_model:
            # 停止音频流
            # 注意：音频流在_setup_internal中创建，这里需要清理
            pass

        self.logger.info("GPTSoVITSOutputProvider清理完成")

    async def _render_internal(self, parameters: RenderParameters):
        """
        内部渲染逻辑

        Args:
            parameters: RenderParameters对象
        """
        # 提取TTS文本
        text = None
        if parameters.tts_enabled:
            text = parameters.tts_text

        if not text or not text.strip():
            self.logger.debug("TTS文本为空，跳过渲染")
            return

        original_text = text.strip()
        self.logger.info(f"准备TTS: '{original_text[:50]}...'")

        final_text = original_text

        # 文本清理（使用缓存的服务引用）
        if self.use_text_cleanup and self._text_cleanup_service:
            try:
                self.logger.debug("使用缓存的 text_cleanup 服务清理文本...")
                cleaned = await self._text_cleanup_service.clean_text(original_text)
                if cleaned:
                    self.logger.info(f"文本经Cleanup服务清理: '{cleaned[:50]}...'")
                    final_text = cleaned
                else:
                    self.logger.warning("Cleanup服务调用失败或返回空，使用原始文本")
            except Exception as e:
                self.logger.error(f"调用 text_cleanup 服务时出错: {e}", exc_info=True)

        if not final_text:
            self.logger.warning("清理后文本为空，跳过TTS")
            return

        # 启动VTS口型同步（使用缓存的服务引用）
        if self.use_vts_lip_sync and self._vts_lip_sync_service:
            try:
                await self._vts_lip_sync_service.start_lip_sync_session(final_text)
            except Exception as e:
                self.logger.debug(f"启动口型同步失败: {e}")

        try:
            # 执行TTS（流式）
            audio_stream = self.tts_model.tts_stream(
                text=final_text,
                text_lang=self.text_language,
                prompt_lang=self.prompt_language,
                top_k=self.top_k,
                top_p=self.top_p,
                temperature=self.temperature,
                speed_factor=self.speed_factor,
                streaming_mode=True,
                media_type="wav",
            )

            # 音频处理和播放
            async for chunk in audio_stream:
                if not chunk:
                    self.logger.debug("收到空音频块，跳过")
                    continue

                # 解析WAV数据并缓冲
                await self._decode_and_buffer(chunk)

                # 首个音频块时触发字幕和VTS口型同步
                if self.use_subtitle or self.use_vts_lip_sync:
                    await self._trigger_subtitle_and_sync(final_text)

            self.logger.info(f"TTS播放完成: '{final_text[:30]}...'")

        except Exception as e:
            self.logger.error(f"TTS渲染失败: {e}", exc_info=True)
            self.error_count += 1
            raise

        self.render_count += 1

    async def _decode_and_buffer(self, wav_chunk):
        """异步解析分块的WAV数据，提取PCM音频并缓冲"""
        try:
            if isinstance(wav_chunk, str):
                wav_data = base64.b64decode(wav_chunk)
            else:
                wav_data = wav_chunk

            async with self.input_pcm_queue_lock:
                is_first_chunk = len(self.input_pcm_queue) == 0

            if is_first_chunk and len(wav_data) >= 44:
                if wav_data[:4] == b"RIFF" and wav_data[8:12] == b"WAVE":
                    self.logger.debug(f"检测到WAV头部，正在解析第一个块，大小: {len(wav_data)} 字节")

                    pos = 12
                    data_found = False
                    while pos < len(wav_data) - 8:
                        chunk_id = wav_data[pos : pos + 4]
                        if chunk_id == b"data":
                            data_found = True
                            break
                        pos += 8

                    if data_found:
                        chunk_size = struct.unpack("<I", wav_data[pos + 4 : pos + 8])[0]
                        data_start = pos + 8
                        data_end = data_start + chunk_size

                        if data_end > len(wav_data):
                            pcm_data = wav_data[data_start:]
                            self.logger.debug(f"从第一个WAV块中提取了 {len(pcm_data)} 字节的PCM数据")
                        else:
                            pcm_data = wav_data[data_start:data_end]
                            self.logger.debug(f"从WAV中提取了 {len(pcm_data)} 字节的PCM数据")
                else:
                    pcm_data = wav_data

        except Exception as e:
            self.logger.error(f"处理WAV数据失败: {str(e)}")
            return

        # 向VTube Studio插件发送音频数据进行口型同步分析（使用缓存的服务引用）
        if pcm_data and len(pcm_data) > 0:
            if self.use_vts_lip_sync and self._vts_lip_sync_service:
                try:
                    await self._vts_lip_sync_service.process_tts_audio(pcm_data, sample_rate=self.sample_rate)
                except Exception as e:
                    self.logger.debug(f"口型同步处理失败: {e}")

        # PCM数据缓冲处理
        async with self.input_pcm_queue_lock:
            self.input_pcm_queue.extend(pcm_data)

        while await self._get_available_pcm_bytes() >= BUFFER_REQUIRED_BYTES:
            if len(self.audio_data_queue) >= self.audio_data_queue.maxlen * 0.9:
                self.logger.warning("音频队列接近满，暂停处理")
                await asyncio.sleep(0.1)
                continue

            raw_block = await self._read_from_pcm_buffer(BUFFER_REQUIRED_BYTES)
            self.audio_data_queue.append(raw_block)

    async def _get_available_pcm_bytes(self):
        """异步获取可用PCM字节数"""
        async with self.input_pcm_queue_lock:
            return len(self.input_pcm_queue)

    async def _read_from_pcm_buffer(self, nbytes):
        """从PCM缓冲区异步读取指定字节数"""
        async with self.input_pcm_queue_lock:
            data = bytes(self.input_pcm_queue)[:nbytes]
            for _ in range(min(nbytes, len(self.input_pcm_queue))):
                self.input_pcm_queue.popleft()
            return data

    async def _trigger_subtitle_and_sync(self, text: str):
        """触发字幕显示和VTS口型同步（仅在首个音频块时调用）"""
        estimated_duration = max(3.0, len(text) * 0.3)

        # 字幕服务（使用缓存的服务引用）
        if self.use_subtitle and self._subtitle_service:
            try:
                await self._subtitle_service.record_speech(text, estimated_duration)
            except Exception as e:
                self.logger.error(f"调用 subtitle_service 出错: {e}", exc_info=True)

        # VTS口型同步（使用缓存的服务引用）
        if self.use_vts_lip_sync and self._vts_lip_sync_service:
            try:
                await self._vts_lip_sync_service.complete_generation()
            except Exception as e:
                self.logger.debug(f"完成口型同步失败: {e}")


class TTSModel:
    """
    GPT-SoVITS TTS模型客户端

    从原gptsovits_tts/plugin.py提取的TTSModel类
    保留所有GPT-SoVITS API调用逻辑
    """

    def __init__(self, host="127.0.0.1", port=9880):
        """
        初始化TTS模型

        Args:
            host: API服务器地址
            port: API服务器端口
        """
        self.host = host
        self.port = port
        self.base_url = f"http://{self.host}:{self.port}"
        self._ref_audio_path = None
        self._prompt_text = ""
        self._current_preset = "default"
        self._initialized = False

    def initialize(self):
        """初始化模型和预设"""
        if self._initialized:
            return
        self._initialized = True

        # 设置默认模型和预设（如配置中有指定）
        # 注意：这里简化了模型和预设管理，原插件有复杂的多预设支持

        # 设置默认参考音频
        if self._ref_audio_path and self._prompt_text:
            pass  # 已在__init__中设置

    def load_preset(self, preset_name: str = "default"):
        """加载指定名称的角色预设"""
        if not self._initialized:
            self.initialize()

        # 简化版本：只加载默认预设
        # 原插件支持多个预设（models.presets），这里暂时简化为默认预设

    def set_refer_audio(self, audio_path: str, prompt_text: str):
        """设置参考音频和对应的提示文本"""
        if not audio_path:
            raise ValueError("audio_path不能为空")
        if not prompt_text:
            raise ValueError("prompt_text不能为空")

        self._ref_audio_path = audio_path
        self._prompt_text = prompt_text

    def set_gpt_weights(self, weights_path):
        """设置GPT权重"""
        response = requests.get(f"{self.base_url}/set_gpt_weights", params={"weights_path": weights_path})
        if response.status_code != 200:
            raise Exception(response.json()["message"])

    def set_sovits_weights(self, weights_path):
        """设置SoVITS权重"""
        response = requests.get(f"{self.base_url}/set_sovits_weights", params={"weights_path": weights_path})
        if response.status_code != 200:
            raise Exception(response.json()["message"])

    def tts(
        self,
        text,
        ref_audio_path=None,
        text_lang=None,
        prompt_text=None,
        prompt_lang=None,
        top_k=None,
        top_p=None,
        temperature=None,
        text_split_method=None,
        batch_size=None,
        batch_threshold=None,
        speed_factor=None,
        streaming_mode=None,
        media_type=None,
        repetition_penalty=None,
        sample_steps=None,
        super_sampling=None,
    ):
        """文本转语音"""
        if not self._initialized:
            self.initialize()

        # 使用传入的ref_audio_path和prompt_text，否则使用持久化的值
        ref_audio_path = ref_audio_path or self._ref_audio_path
        if not ref_audio_path:
            raise ValueError("未设置参考音频，请先调用set_refer_audio设置参考音频和提示文本")

        prompt_text = prompt_text if prompt_text is not None else self._prompt_text

        # 语言检测（自动）
        if text_lang == "auto":
            has_english = bool(re.search("[a-zA-Z]", text))
            if not has_english:
                text_lang = "zh"

        params = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": ref_audio_path,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang or "zh",
            "top_k": top_k,
            "top_p": top_p,
            "temperature": temperature,
            "text_split_method": text_split_method,
            "batch_size": batch_size,
            "batch_threshold": batch_threshold,
            "speed_factor": speed_factor,
            "streaming_mode": streaming_mode if streaming_mode is not None else True,
            "media_type": media_type or "wav",
            "repetition_penalty": repetition_penalty,
            "sample_steps": sample_steps,
            "super_sampling": super_sampling if super_sampling is not None else True,
        }

        response = requests.get(f"{self.base_url}/tts", params=params, timeout=60)
        if response.status_code != 200:
            raise Exception(response.json()["message"])
        return response.content

    def tts_stream(
        self,
        text,
        ref_audio_path=None,
        text_lang=None,
        prompt_text=None,
        prompt_lang=None,
        top_k=None,
        top_p=None,
        temperature=None,
        text_split_method=None,
        batch_size=None,
        batch_threshold=None,
        speed_factor=None,
        media_type=None,
        repetition_penalty=None,
        sample_steps=None,
        super_sampling=None,
    ):
        """流式文本转语音，返回音频数据流"""
        if not self._initialized:
            self.initialize()

        ref_audio_path = ref_audio_path or self._ref_audio_path
        if not ref_audio_path:
            raise ValueError("未设置参考音频")

        prompt_text = prompt_text if prompt_text is not None else self._prompt_text

        # 语言检测（自动）
        if text_lang == "auto":
            has_english = bool(re.search("[a-zA-Z]", text))
            if not has_english:
                text_lang = "zh"

        params = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": ref_audio_path,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang or "zh",
            "top_k": top_k,
            "top_p": top_p,
            "temperature": temperature,
            "text_split_method": text_split_method,
            "batch_size": batch_size,
            "batch_threshold": batch_threshold,
            "speed_factor": speed_factor,
            "streaming_mode": True,
            "media_type": "wav",
            "repetition_penalty": repetition_penalty,
            "sample_steps": sample_steps,
            "super_sampling": super_sampling if super_sampling is not None else True,
        }

        response = requests.get(
            f"{self.base_url}/tts",
            params=params,
            stream=True,
            timeout=(3.05, None),
            headers={"Connection": "keep-alive"},
        )

        if response.status_code != 200:
            raise Exception(response.json()["message"])

        return response.iter_content(chunk_size=4096)
