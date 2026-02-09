# Amaidesu TTS Plugin: src/plugins/tts/plugin.py

import asyncio
import logging
import os
import sys
import struct  # 添加struct模块导入，用于解析WAV数据
from typing import Dict, Any, Optional
import numpy as np  # 确保导入 numpy
from collections import deque
import base64
import re  # 添加正则表达式模块，用于检测英文字符

# --- Dependencies Check (Inform User) ---
# Try importing required libraries and inform the user if they are missing.
# Actual error will be caught later if import fails during use.
dependencies_ok = True
try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    print("依赖缺失: 请运行 'pip install sounddevice soundfile' 来使用音频播放功能。", file=sys.stderr)
    dependencies_ok = False

# --- 远程流支持 ---
try:
    from src.plugins.remote_stream.plugin import RemoteStreamService, StreamMessage, MessageType

    REMOTE_STREAM_AVAILABLE = True
except ImportError:
    REMOTE_STREAM_AVAILABLE = False
    print("提示: 未找到 remote_stream 插件，将使用本地音频输出。", file=sys.stderr)

# try:
#     from openai import AsyncOpenAI, APIConnectionError, RateLimitError, APIStatusError
# except ImportError:
#     pass # openai is optional for this plugin now

# --- TOML Loading ---
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import toml as tomllib
    except ImportError:
        print("依赖缺失: 请运行 'pip install toml' 来加载 TTS 插件配置。", file=sys.stderr)
        tomllib = None
        dependencies_ok = False

# --- Amaidesu Core Imports ---
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from maim_message import MessageBase, Seg  # Import MessageBase for type hint

logger = logging.getLogger(__name__)

# --- Plugin Configuration Loading ---
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_PLUGIN_DIR, "config.toml")


# 音频流参数（根据实际播放器配置）
CHANNELS = 1  # 声道数
DTYPE = np.int16  # 样本类型
BLOCKSIZE = 1024  # 每次播放的帧数

# 计算每块音频数据字节数
SAMPLE_SIZE = DTYPE().itemsize  # 单个样本大小（如 np.int16 → 2 bytes）
BUFFER_REQUIRED_BYTES = BLOCKSIZE * CHANNELS * SAMPLE_SIZE

from dataclasses import dataclass
from typing import List
import toml


@dataclass
class TTSPreset:
    name: str
    ref_audio: str
    prompt_text: str
    gpt_model: str = ""
    sovits_model: str = ""


@dataclass
class TTSModels:
    gpt_model: str
    sovits_model: str
    presets: Dict[str, TTSPreset]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TTSModels":
        presets = {name: TTSPreset(**preset_data) for name, preset_data in data.get("presets", {}).items()}
        return cls(
            gpt_model=data.get("gpt_model", ""),
            sovits_model=data.get("sovits_model", ""),
            presets=presets,
        )


@dataclass
class TTSConfig:
    # api_url: str
    host: str
    port: int
    sample_rate: int
    ref_audio_path: str
    prompt_text: str
    aux_ref_audio_paths: List[str]
    text_language: str
    prompt_language: str
    media_type: str
    streaming_mode: bool
    top_k: int
    top_p: float
    temperature: float
    batch_size: int
    batch_threshold: float
    speed_factor: float
    text_split_method: str
    repetition_penalty: float
    sample_steps: int
    super_sampling: bool
    models: TTSModels

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TTSConfig":
        models_data = data.pop("models", {})
        return cls(
            **{k: v for k, v in data.items() if k != "models"},
            models=TTSModels.from_dict(models_data),
        )


@dataclass
class PluginConfig:
    output_device: str
    llm_clean: bool
    lip_sync_service_name: str
    use_remote_stream: bool = False  # 是否使用远程流服务

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginConfig":
        return cls(
            output_device=data.get("output_device_name", ""),
            llm_clean=data.get("llm_clean", True),
            lip_sync_service_name=data.get("lip_sync_service_name", "vts_lip_sync"),
            use_remote_stream=data.get("use_remote_stream", False),
        )


@dataclass
class PipelineConfig:
    default_preset: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineConfig":
        return cls(
            default_preset=data.get("default_preset", "default"),
        )


@dataclass
class BaseConfig:
    tts: TTSConfig
    pipeline: PipelineConfig
    plugin: PluginConfig

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaseConfig":
        return cls(
            tts=TTSConfig.from_dict(data["tts"]),
            pipeline=PipelineConfig.from_dict(data.get("pipeline", {})),
            plugin=PluginConfig.from_dict(data.get("plugin", {})),
        )


class Config:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config_data = load_config(config_path)
        self.base_config = BaseConfig.from_dict(self.config_data)

    def __getitem__(self, key: str) -> Any:
        return self.config_data[key]

    def __setitem__(self, key: str, value: Any):
        self.config_data[key] = value

    def __repr__(self) -> str:
        return str(self.config_data)

    @property
    def tts(self) -> TTSConfig:
        return self.base_config.tts

    @property
    def pipeline(self) -> PipelineConfig:
        return self.base_config.pipeline

    @property
    def plugin(self) -> PluginConfig:
        return self.base_config.plugin


def load_config(config_path: str) -> Dict[str, Any]:
    """加载TOML配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        配置字典
    """
    with open(config_path, "r", encoding="utf-8") as f:
        config = toml.load(f)
    return config


def get_default_config() -> Config:
    """获取默认配置"""
    config_path = _CONFIG_FILE
    return Config(str(config_path))


import requests
import os
from typing import Dict, Any


class TTSModel:
    def __init__(self, config: Config = None, host="127.0.0.1", port=9880):
        """初始化TTS模型

        Args:
            config: 配置对象,如果为None则使用默认配置
            host: API服务器地址
            port: API服务器端口
        """
        self.config = config
        if config:
            self.host = config.tts.host
            self.port = config.tts.port
        else:
            self.host = host
            self.port = port

        self.base_url = f"http://{self.host}:{self.port}"
        self._ref_audio_path = None  # 存储当前使用的参考音频路径
        self._prompt_text = ""  # 存储当前使用的提示文本
        self._current_preset = "default"  # 当前使用的角色预设名称
        self._initialized = False  # 标记是否已完成初始化

    def initialize(self):
        """初始化模型和预设

        如果已经初始化过，则跳过
        """
        if self._initialized:
            return
        self._initialized = True

        # 初始化默认模型
        if self.config:
            if self.config.tts.models.gpt_model:
                self.set_gpt_weights(self.config.tts.models.gpt_model)
            if self.config.tts.models.sovits_model:
                self.set_sovits_weights(self.config.tts.models.sovits_model)

        # 设置默认角色预设
        if self.config:
            self.load_preset("default")

    @property
    def ref_audio_path(self):
        """获取当前使用的参考音频路径"""
        return self._ref_audio_path

    @property
    def prompt_text(self):
        """获取当前使用的提示文本"""
        return self._prompt_text

    @property
    def current_preset(self):
        """获取当前使用的角色预设名称"""
        return self._current_preset

    def get_preset(self, preset_name: str) -> Optional[Dict[str, Any]]:
        """获取指定名称的角色预设配置

        Args:
            preset_name: 预设名称

        Returns:
            预设配置字典，如果不存在则返回None
        """
        if not self.config:
            return None

        presets = self.config.tts.models.presets
        return presets.get(preset_name)

    def load_preset(self, preset_name: str):
        """加载指定的角色预设

        Args:
            preset_name: 预设名称

        Raises:
            ValueError: 当预设不存在时抛出
        """
        if not self._initialized:
            self.initialize()

        preset = self.get_preset(preset_name)
        if not preset:
            raise ValueError(f"预设 {preset_name} 不存在")

        # 设置参考音频和提示文本
        self.set_refer_audio(preset.ref_audio, preset.prompt_text)

        # 如果预设指定了模型，则切换模型
        if preset.gpt_model:
            self.set_gpt_weights(preset.gpt_model)
        if preset.sovits_model:
            self.set_sovits_weights(preset.sovits_model)

        self._current_preset = preset_name

    def set_refer_audio(self, audio_path: str, prompt_text: str):
        """设置参考音频和对应的提示文本

        Args:
            audio_path: 音频文件路径
            prompt_text: 对应的提示文本，必须提供

        Raises:
            ValueError: 当参数无效时抛出异常
        """
        if not audio_path:
            raise ValueError("audio_path不能为空")
        if not prompt_text:
            raise ValueError("prompt_text不能为空")

        # if not os.path.exists(audio_path):
        #     raise ValueError(f"音频文件不存在: {audio_path}")

        self._ref_audio_path = audio_path
        self._prompt_text = prompt_text

    def set_gpt_weights(self, weights_path):
        """设置GPT权重"""
        # if not os.path.exists(weights_path):
        #     raise ValueError(f"GPT模型文件不存在: {weights_path}")

        response = requests.get(f"{self.base_url}/set_gpt_weights", params={"weights_path": weights_path})
        if response.status_code != 200:
            raise Exception(response.json()["message"])

    def set_sovits_weights(self, weights_path):
        """设置SoVITS权重"""
        # if not os.path.exists(weights_path):
        #     raise ValueError(f"SoVITS模型文件不存在: {weights_path}")

        response = requests.get(f"{self.base_url}/set_sovits_weights", params={"weights_path": weights_path})
        if response.status_code != 200:
            raise Exception(response.json()["message"])

    def tts(
        self,
        text,
        ref_audio_path=None,
        aux_ref_audio_paths=None,
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
        """文本转语音

        Args:
            text: 要合成的文本
            ref_audio_path: 参考音频路径，如果为None则使用上次设置的参考音频
            aux_ref_audio_paths: 辅助参考音频路径列表(用于多说话人音色融合)
            prompt_text: 提示文本，如果为None则使用上次设置的提示文本
            text_lang: 文本语言,默认使用配置文件中的设置
            prompt_lang: 提示文本语言,默认使用配置文件中的设置
            top_k: top k采样
            top_p: top p采样
            temperature: 温度系数
            text_split_method: 文本分割方法
            batch_size: 批处理大小
            batch_threshold: 批处理阈值
            speed_factor: 语速控制
            streaming_mode: 是否启用流式输出
            media_type: 音频格式(wav/raw/ogg/aac)
            repetition_penalty: 重复惩罚系数
            sample_steps: VITS采样步数
            super_sampling: 是否启用超采样
        """
        if not self._initialized:
            self.initialize()

        # 优先使用传入的ref_audio_path和prompt_text,否则使用持久化的值
        ref_audio_path = ref_audio_path or self._ref_audio_path
        if not ref_audio_path:
            raise ValueError("未设置参考音频，请先调用set_refer_audio设置参考音频和提示文本")

        prompt_text = prompt_text if prompt_text is not None else self._prompt_text

        # 使用配置文件中的默认值
        if self.config:
            cfg = self.config.tts
            text_lang = text_lang or cfg.text_language

            # 如果设置的text_lang是auto，检测文本是否包含英文字符
            if text_lang == "auto":
                # 检查是否包含英文字符
                has_english = bool(re.search("[a-zA-Z]", text))
                if not has_english:
                    text_lang = "zh"  # 如果不含英文字符，则设为中文
                # 含英文字符，保持auto不变

            prompt_lang = prompt_lang or cfg.prompt_language
            media_type = media_type or cfg.media_type
            streaming_mode = streaming_mode if streaming_mode is not None else cfg.streaming_mode
            top_k = top_k or cfg.top_k
            top_p = top_p or cfg.top_p
            temperature = temperature or cfg.temperature
            text_split_method = text_split_method or cfg.text_split_method
            batch_size = batch_size or cfg.batch_size
            batch_threshold = batch_threshold or cfg.batch_threshold
            speed_factor = speed_factor or cfg.speed_factor
            repetition_penalty = repetition_penalty or cfg.repetition_penalty
            sample_steps = sample_steps or cfg.sample_steps
            super_sampling = super_sampling if super_sampling is not None else cfg.super_sampling

        # 检测文本是否包含英文字符
        contains_english = bool(re.search(r"[a-zA-Z]", text))

        # 如果文本包含英文字符且未指定文本语言，则自动设置为英文
        if contains_english and text_lang is None:
            text_lang = "en"

        params = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": ref_audio_path,
            "aux_ref_audio_paths": aux_ref_audio_paths,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang,
            "top_k": top_k,
            "top_p": top_p,
            "temperature": temperature,
            "text_split_method": text_split_method,
            "batch_size": batch_size,
            "batch_threshold": batch_threshold,
            "speed_factor": speed_factor,
            "streaming_mode": streaming_mode,
            "media_type": media_type,
            "repetition_penalty": repetition_penalty,
            "sample_steps": sample_steps,
            "super_sampling": super_sampling,
        }
        # print(f"请求参数: {params}")
        response = requests.get(f"{self.base_url}/tts", params=params, timeout=60)
        if response.status_code != 200:
            raise Exception(response.json()["message"])
        return response.content

    def tts_stream(
        self,
        text,
        ref_audio_path=None,
        aux_ref_audio_paths=None,
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
        """流式文本转语音,返回音频数据流

        参数与tts()方法相同,但streaming_mode强制为True
        """
        if not self._initialized:
            self.initialize()

        # 优先使用传入的ref_audio_path和prompt_text,否则使用持久化的值
        ref_audio_path = ref_audio_path or self._ref_audio_path
        if not ref_audio_path:
            raise ValueError("未设置参考音频")

        prompt_text = prompt_text if prompt_text is not None else self._prompt_text

        # 使用配置文件默认值
        if self.config:
            cfg = self.config.tts
            text_lang = text_lang or cfg.text_language

            # 如果设置的text_lang是auto，检测文本是否包含英文字符
            if text_lang == "auto":
                # 检查是否包含英文字符
                has_english = bool(re.search("[a-zA-Z]", text))
                if not has_english:
                    text_lang = "zh"  # 如果不含英文字符，则设为中文
                # 含英文字符，保持auto不变

            prompt_lang = prompt_lang or cfg.prompt_language
            media_type = media_type or cfg.media_type
            top_k = top_k or cfg.top_k
            top_p = top_p or cfg.top_p
            temperature = temperature or cfg.temperature
            text_split_method = text_split_method or cfg.text_split_method
            batch_size = batch_size or cfg.batch_size
            batch_threshold = batch_threshold or cfg.batch_threshold
            speed_factor = speed_factor or cfg.speed_factor
            repetition_penalty = repetition_penalty or cfg.repetition_penalty
            sample_steps = sample_steps or cfg.sample_steps
            super_sampling = super_sampling if super_sampling is not None else cfg.super_sampling

        params = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": ref_audio_path,
            "aux_ref_audio_paths": aux_ref_audio_paths,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang,
            "top_k": top_k,
            "top_p": top_p,
            "temperature": temperature,
            "text_split_method": text_split_method,
            "batch_size": batch_size,
            "batch_threshold": batch_threshold,
            "speed_factor": speed_factor,
            "streaming_mode": True,  # 强制使用流式模式
            "media_type": "wav",
            "repetition_penalty": repetition_penalty,
            "sample_steps": sample_steps,
            "super_sampling": super_sampling,
        }

        # print(f"流式请求参数: {params}")

        # 使用自定义超时，并设置较小的块大小来保持流式传输的响应性
        response = requests.get(
            f"{self.base_url}/tts",
            params=params,
            stream=True,
            timeout=(3.05, None),  # (连接超时, 读取超时)
            headers={"Connection": "keep-alive"},
        )

        if response.status_code != 200:
            raise Exception(response.json()["message"])

        # 使用更小的块大小来提高流式传输的响应性
        return response.iter_content(chunk_size=4096)


class TTSPlugin(BasePlugin):
    """处理文本消息，执行 TTS 播放，可选 Cleanup LLM 和 UDP 广播。"""

    _is_amaidesu_plugin: bool = True  # Plugin marker

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        # Note: plugin_config from PluginManager is the global [plugins] config
        # We load our own specific config here.
        super().__init__(core, plugin_config)
        self.tts_config = get_default_config()

        # --- 服务缓存 ---
        self.vts_lip_sync_service = None

        # --- 远程流支持 ---
        self.use_remote_stream = self.tts_config.plugin.use_remote_stream
        self.remote_stream_service = None

        # --- TTS Service Initialization (from tts_service.py) ---
        self.output_device_name = self.tts_config.plugin.output_device or ""
        self.output_device_index = self._find_device_index(self.output_device_name, kind="output")
        self.tts_lock = asyncio.Lock()
        # 为消息处理添加专门的锁
        self.message_lock = asyncio.Lock()

        self.vts_lip_sync_service = None
        # 使用threading.Lock而不是asyncio.Lock，因为decode_and_buffer是同步方法
        self.input_pcm_queue_lock = asyncio.Lock()

        self.logger.info(f"TTS 服务组件初始化。输出设备: {self.output_device_name or '默认设备'}")
        self.tts_model = TTSModel(self.tts_config, self.tts_config.tts.host, self.tts_config.tts.port)
        self.input_pcm_queue = deque(b"")
        # 为音频数据队列添加最大长度限制，防止内存占用过高
        self.audio_data_queue = deque(maxlen=1000)  # 限制缓冲区大小，防止内存占用过高

        # 当前处理消息数据
        self.msg_id = ""
        self.message = None

        self.stream = None

        # --- UDP Broadcast Initialization (from tts_monitor.py / mmc_client.py) ---

    def _find_device_index(self, device_name: Optional[str], kind: str = "output") -> Optional[int]:
        """根据设备名称查找设备索引 (来自 tts_service.py)。"""
        if "sd" not in globals():  # Check if sounddevice was imported
            self.logger.error("sounddevice 库不可用，无法查找音频设备。")
            return None
        try:
            devices = sd.query_devices()
            if device_name:
                for i, device in enumerate(devices):
                    # Case-insensitive partial match
                    max_channels_key = f"max_{kind}_channels"
                    if device_name.lower() in device["name"].lower() and device[max_channels_key] > 0:
                        self.logger.info(f"找到 {kind} 设备 '{device['name']}' (匹配 '{device_name}')，索引: {i}")
                        return i
                self.logger.warning(f"未找到名称包含 '{device_name}' 的 {kind} 设备，将使用默认设备。")

            # Determine default device index based on kind
            default_device_indices = sd.default.device
            default_index = default_device_indices[1] if kind == "output" else default_device_indices[0]
            if default_index == -1:  # Indicates no default device found by sounddevice
                self.logger.warning(f"未找到默认 {kind} 设备，将使用 None (由 sounddevice 选择)。")
                return None

            self.logger.info(f"使用默认 {kind} 设备索引: {default_index} ({sd.query_devices(default_index)['name']})")
            return default_index
        except Exception as e:
            self.logger.error(f"查找音频设备时出错: {e}，将使用 None (由 sounddevice 选择)", exc_info=True)
            return None

    async def decode_and_buffer(self, wav_chunk):
        """异步解析分块的WAV数据，提取PCM音频并缓冲

        Args:
            wav_chunk: 字节格式的WAV切块数据（可能是Base64编码的）
        """
        # 检查是否为Base64编码的数据
        try:
            # 尝试Base64解码
            if isinstance(wav_chunk, str):
                wav_data = base64.b64decode(wav_chunk)
            else:
                wav_data = wav_chunk  # 已经是字节格式

            # 保存第一个块的WAV头信息，用于后续处理
            async with self.input_pcm_queue_lock:
                is_first_chunk = len(self.input_pcm_queue) == 0

            # 解析WAV头
            if is_first_chunk and len(wav_data) >= 44:  # 标准WAV头至少44字节
                # 验证是否是WAV格式的第一个块
                if wav_data[:4] == b"RIFF" and wav_data[8:12] == b"WAVE":
                    self.logger.debug(f"检测到WAV头部，正在解析第一个块，大小: {len(wav_data)} 字节")

                    # 查找data块位置（搜索"data"标识符）
                    pos = 12
                    data_found = False
                    while pos < len(wav_data) - 8:  # 至少需要8字节来读取块ID和长度
                        chunk_id = wav_data[pos : pos + 4]
                        if chunk_id == b"data":
                            data_found = True
                            break
                        chunk_size = struct.unpack("<I", wav_data[pos + 4 : pos + 8])[0]
                        pos += 8 + chunk_size

                    if data_found:
                        # 提取PCM数据
                        chunk_size = struct.unpack("<I", wav_data[pos + 4 : pos + 8])[0]
                        data_start = pos + 8
                        data_end = data_start + chunk_size

                        if data_end > len(wav_data):
                            # 当前块只包含部分PCM数据
                            pcm_data = wav_data[data_start:]
                            self.logger.debug(f"从第一个WAV块中提取了 {len(pcm_data)} 字节的PCM数据")
                        else:
                            # 当前块包含完整PCM数据
                            pcm_data = wav_data[data_start:data_end]
                            self.logger.debug(f"从WAV中提取了 {len(pcm_data)} 字节的PCM数据")
                    else:
                        # WAV头部块可能不包含data块，直接处理下一个块
                        self.logger.debug("第一个块可能只包含WAV头部信息，未找到data块")
                        return
                else:
                    # 不是WAV格式或是后续数据块，直接当作PCM数据处理
                    # self.logger.debug("收到的不是WAV格式数据或为后续数据块，当作PCM数据处理")
                    pcm_data = wav_data
            else:
                # 后续块或非WAV格式，直接当作PCM数据处理
                # self.logger.debug(f"处理非WAV头部数据块，大小: {len(wav_data)} 字节，当作PCM数据处理")
                pcm_data = wav_data

        except Exception as e:
            self.logger.error(f"处理WAV数据失败: {str(e)}")
            return

        # --- 向VTube Studio插件发送音频数据进行口型同步分析 ---
        if pcm_data and len(pcm_data) > 0:
            if self.vts_lip_sync_service:
                try:
                    # 异步发送音频数据进行口型同步分析
                    self.logger.debug(f"发送音频数据进行口型同步分析: {len(pcm_data)}")
                    await self.vts_lip_sync_service.process_tts_audio(
                        pcm_data, sample_rate=self.tts_config.tts.sample_rate
                    )
                except Exception as e:
                    self.logger.debug(f"口型同步处理失败: {e}")

        # PCM数据缓冲处理
        async with self.input_pcm_queue_lock:
            self.input_pcm_queue.extend(pcm_data)
            # self.logger.debug(f"缓冲 {len(pcm_data)} 字节的PCM数据")

        # 按需切割音频块
        while await self.get_available_pcm_bytes() >= BUFFER_REQUIRED_BYTES:
            # 检查音频队列长度，防止队列过长
            if len(self.audio_data_queue) >= self.audio_data_queue.maxlen * 0.9:  # 接近队列上限的90%
                self.logger.warning("音频队列接近满，暂停处理")
                # 短暂等待，让音频播放追赶队列
                await asyncio.sleep(0.1)
                continue

            raw_block = await self.read_from_pcm_buffer(BUFFER_REQUIRED_BYTES)
            self.audio_data_queue.append(raw_block)
            # self.logger.debug(f"成功添加 {BUFFER_REQUIRED_BYTES} 字节到音频播放队列")

    def start_pcm_stream(self, samplerate=44100, channels=2, dtype=np.int16, blocksize=1024):
        """创建并启动音频流

        参数:
            samplerate: 采样率（推荐44100/48000）
            channels: 声道数（1=单声道，2=立体声）
            dtype: 数据类型（一般使用np.int16或np.float32）
            blocksize: 每次处理的帧数
        """

        def audio_callback(outdata, frames, time, status):
            try:
                pcm_data = self.audio_data_queue.popleft()
                outdata[:] = np.frombuffer(pcm_data, dtype=DTYPE).reshape(-1, CHANNELS)
            except IndexError:
                # 播放队列为空时阻塞输出（系统会自动保持）
                outdata.fill(0)

        # 创建音频流
        stream = sd.OutputStream(
            samplerate=samplerate,
            channels=channels,
            dtype=dtype,
            blocksize=blocksize,
            callback=audio_callback,
            device=self.output_device_index,
        )

        return stream

    async def get_available_pcm_bytes(self):
        """异步获取可用PCM字节数"""
        async with self.input_pcm_queue_lock:
            return len(self.input_pcm_queue)

    async def read_from_pcm_buffer(self, nbytes):
        """从PCM缓冲区异步读取指定字节数"""
        async with self.input_pcm_queue_lock:
            data = bytes(self.input_pcm_queue)[:nbytes]
            # 从队列中删除已经读取的字节
            for _ in range(min(nbytes, len(self.input_pcm_queue))):
                self.input_pcm_queue.popleft()
            return data

    async def setup(self):
        """注册处理来自 MaiCore 的 'text' 类型消息。"""
        await super().setup()
        # 注册处理函数，监听所有 WebSocket 消消息
        # 我们将在处理函数内部检查消息类型是否为 'text'
        self.core.register_websocket_handler("*", self.handle_maicore_message)
        self.logger.info("TTS 插件已设置，监听所有 MaiCore WebSocket 消息。")

        # 设置远程流（如果启用）
        if self.use_remote_stream:
            # 获取 remote_stream 服务
            remote_stream_service = self.core.get_service("remote_stream")
            if remote_stream_service:
                self.remote_stream_service = remote_stream_service
                self.logger.info("已获取 Remote Stream 服务，将使用远程音频输出")

        # 只有在不使用远程流时才启动本地音频流
        if not self.use_remote_stream:
            self.stream = self.start_pcm_stream(
                samplerate=self.tts_config.tts.sample_rate,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCKSIZE,
            )
            self.logger.info("本地音频流已启动。")

        self.tts_model.load_preset(self.tts_config.pipeline.default_preset)

    async def cleanup(self):
        """关闭 UDP socket。"""
        if self.stream:
            self.stream.stop()
            self.stream.close()
        # 可以考虑添加取消正在进行的 TTS 的逻辑
        await super().cleanup()

    async def handle_maicore_message(self, message: MessageBase):
        """处理从 MaiCore 收到的消息，如果是文本类型，则进行 TTS 处理。"""
        # 使用消息锁确保同一时间只处理一条消息
        async with self.message_lock:
            self.logger.debug("获取消息处理锁，开始处理消息")

            # 检查消息段是否存在且类型为 'text'
            def process_seg(seg: Seg) -> str:
                text = ""
                if seg.type == "seglist":
                    for s in seg.data:
                        text += process_seg(s)
                elif seg.type == "tts_text":
                    # 用冒号分割，取第一个和后面的所有
                    msg_id, text = seg.data.split(":", 1)
                    self.msg_id = msg_id
                    self.logger.info(f"收到TTS文本消息，msg_id: {msg_id}, text: {text}")
                elif seg.type == "reply":
                    # 处理回复类型的seg，通过消息缓存服务获取原始消息内容
                    message_cache_service = self.core.get_service("message_cache")
                    if message_cache_service:
                        try:
                            original_message = message_cache_service.get_message(seg.data)
                            if original_message:
                                # 递归处理原始消息的内容
                                if original_message.message_segment:
                                    reply_text = process_seg(original_message.message_segment)
                                    text = f"回复{reply_text},"
                                else:
                                    text = ""
                            else:
                                text = ""
                        except Exception as e:
                            self.logger.error(f"获取回复消息内容失败: {e}")
                            text = ""
                    else:
                        text = ""
                return text

            self.message = message

            if message.message_segment:
                original_text = process_seg(message.message_segment)
                if not isinstance(original_text, str) or not original_text.strip():
                    self.logger.debug("收到非字符串或空文本消息段，跳过 TTS。")
                    return

                original_text = original_text.strip()
                self.logger.info(f"收到文本消息，准备 TTS: '{original_text[:50]}...'")

                final_text = original_text

                # 1. (可选) 清理文本 - 通过服务调用
                cleanup_service = self.core.get_service("text_cleanup")
                if cleanup_service:
                    self.logger.debug("找到 text_cleanup 服务，尝试清理文本...")
                    try:
                        # 确保调用的是 await clean_text(text)
                        cleaned = await cleanup_service.clean_text(original_text)
                        if cleaned:
                            self.logger.info(
                                f"文本经 Cleanup 服务清理: '{cleaned[:50]}...' (原: '{original_text[:50]}...')"
                            )
                            final_text = cleaned
                        else:
                            self.logger.warning("Cleanup 服务调用失败或返回空，使用原始文本。")
                    except AttributeError:
                        self.logger.error("获取到的 'text_cleanup' 服务没有 'clean_text' 方法。")
                    except Exception as e:
                        self.logger.error(f"调用 text_cleanup 服务时出错: {e}", exc_info=True)
                else:
                    # 如果配置中 cleanup_llm.enable 为 true 但服务未注册，可能需要警告
                    cleanup_config_in_tts = self.tts_config.plugin.llm_clean
                    if cleanup_config_in_tts.get("enable", False):
                        self.logger.warning(
                            "Cleanup LLM 在 TTS 配置中启用，但未找到 'text_cleanup' 服务。请确保 CleanupLLMPlugin 已启用并成功加载。"
                        )
                    else:
                        self.logger.debug("未找到 text_cleanup 服务 (可能未启用 CleanupLLMPlugin)。")

                if not final_text:
                    self.logger.warning("清理后文本为空，跳过后续处理。")
                    return
                # 3. 执行 TTS
                await self._speak(final_text)
            elif message.message_segment:
                # 处理其他类型的消息段，包括 reply 类型
                processed_text = process_seg(message.message_segment)
                if processed_text and processed_text.strip():
                    self.logger.info(f"收到非文本类型消息，处理后准备 TTS: '{processed_text[:50]}...'")
                    final_text = processed_text.strip()

                    # 执行相同的清理和TTS流程
                    cleanup_service = self.core.get_service("text_cleanup")
                    if cleanup_service:
                        self.logger.debug("找到 text_cleanup 服务，尝试清理文本...")
                        try:
                            cleaned = await cleanup_service.clean_text(final_text)
                            if cleaned:
                                self.logger.info(
                                    f"文本经 Cleanup 服务清理: '{cleaned[:50]}...' (原: '{final_text[:50]}...')"
                                )
                                final_text = cleaned
                            else:
                                self.logger.warning("Cleanup 服务调用失败或返回空，使用原始文本。")
                        except AttributeError:
                            self.logger.error("获取到的 'text_cleanup' 服务没有 'clean_text' 方法。")
                        except Exception as e:
                            self.logger.error(f"调用 text_cleanup 服务时出错: {e}", exc_info=True)

                    if final_text:
                        await self._speak(final_text)
                    else:
                        self.logger.warning("处理后文本为空，跳过 TTS。")
                else:
                    self.logger.debug("处理后文本为空，跳过 TTS。")
            else:
                # 可以选择性地记录收到的非文本消息
                # msg_type = message.message_segment.type if message.message_segment else "No Segment"
                # self.logger.debug(f"收到非文本类型消息 ({msg_type})，TTS 插件跳过。")
                pass

    async def _speak(self, text: str):
        self.logger.info(f"请求播放: '{text[:30]}...'")

        vts_lip_sync_service = self.core.get_service("vts_lip_sync")
        if vts_lip_sync_service:
            try:
                await lip_sync_service.start_lip_sync_session(text)
            except Exception as e:
                self.logger.debug(f"启动口型同步会话失败: {e}")

        try:
            # 发起流式请求（不阻塞，但首 chunk 可能延迟）
            audio_stream = self.tts_model.tts_stream(text)
            self.logger.debug("TTS 流已创建，等待首音频块...")

            # 确保音频播放流已启动
            if self.stream and not self.stream.active:
                self.stream.start()

            # 标记是否已发送字幕（避免重复）
            subtitle_sent = False

            # 开始消费音频流
            for chunk in audio_stream:
                if not chunk:
                    self.logger.debug("收到空音频块，跳过")
                    continue

                # 👇 第一次收到有效音频块时，立即发送字幕
                if not subtitle_sent:
                    self.logger.debug("收到首个音频块，触发字幕显示")

                    # 发送 OBS 字幕
                    obs_service = self.core.get_service("obs_control")
                    if obs_service:
                        try:
                            await obs_service.send_to_obs(text)
                        except Exception as e:
                            self.logger.error(f"向 OBS 发送字幕失败: {e}", exc_info=True)

                    # 通知字幕服务
                    subtitle_service = self.core.get_service("subtitle_service")
                    if subtitle_service:
                        try:
                            # 动态估算时长
                            estimated_duration = max(3.0, len(text) * 0.3)
                            asyncio.create_task(subtitle_service.record_speech(text, estimated_duration))
                        except Exception as e:
                            self.logger.error(f"调用 subtitle_service 出错: {e}", exc_info=True)

                    subtitle_sent = True  # 只发一次

                # 处理音频（播放 + 口型同步）
                await self.decode_and_buffer(chunk)

            self.logger.info(f"音频播放完成: '{text[:30]}...'")

        except Exception as e:
            self.logger.error(f"TTS 播放出错: {e}", exc_info=True)
        finally:
            if vts_lip_sync_service:
                try:
                    await reply_manager.complete_generation()
                    self.logger.debug("回复生成页面已完成")
                except Exception as e:
                    self.logger.debug(f"停止口型同步失败: {e}")


plugin_entrypoint = TTSPlugin
