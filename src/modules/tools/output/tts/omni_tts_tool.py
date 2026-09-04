"""
OmniTTSProvider - Omni TTS 语音合成工具

- 仅迁移不改造（不准合并到 GPTSoVITS，不补文本清洗，
  不修复 KeyError 已知问题，保持现状）
- 引擎：GPT-SoVITS HTTP 流式请求 + wav 解码 + AudioDeviceManager 播放
- ToolProvider 协议由本类自身实现
- 工具 ``omni_tts_synthesize(text)`` 调用 ``handle_speech()`` 入口
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec

if TYPE_CHECKING:
    pass


# 检查依赖
TTS_DEPENDENCIES_OK = False
try:
    import requests

    TTS_DEPENDENCIES_OK = True
except ImportError:
    pass


_OMNI_TTS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "要合成的文本"},
    },
    "required": ["text"],
}


class OmniTTSProvider:
    """Omni TTS ToolProvider"""

    PROVIDER_NAME = "omni_tts"

    class ConfigSchema(BaseConfig):
        """Omni TTS 配置"""

        type: str = "omni_tts"
        host: str = Field(default="127.0.0.1", description="API 主机地址")
        port: int = Field(default=9880, ge=1, le=65535, description="API 端口")
        ref_audio_path: str = Field(default="", description="参考音频路径")
        prompt_text: str = Field(default="", description="提示文本")
        text_language: str = Field(default="zh", pattern=r"^(zh|en|ja|auto)$", description="文本语言")
        prompt_language: str = Field(default="zh", pattern=r"^(zh|en|ja)$", description="提示语言")
        top_k: int = Field(default=20, ge=1, le=100, description="Top-K 采样")
        top_p: float = Field(default=0.6, ge=0.0, le=1.0, description="Top-P 采样")
        temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="温度参数")
        speed_factor: float = Field(default=1.0, ge=0.1, le=3.0, description="语速因子")
        streaming_mode: bool = Field(default=True, description="是否启用流式模式")
        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")
        sample_rate: int = Field(default=32000, ge=8000, le=48000, description="采样率")
        use_text_cleanup: bool = Field(default=True, description="是否使用文本清理")
        use_vts_lip_sync: bool = Field(default=True, description="是否使用 VTS 口型同步")
        use_subtitle: bool = Field(default=True, description="是否使用字幕")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(config)

        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.base_url = f"http://{self.host}:{self.port}"
        self.ref_audio_path = self.typed_config.ref_audio_path
        self.prompt_text = self.typed_config.prompt_text
        self.text_language = self.typed_config.text_language
        self.prompt_language = self.typed_config.prompt_language
        self.top_k = self.typed_config.top_k
        self.top_p = self.typed_config.top_p
        self.temperature = self.typed_config.temperature
        self.speed_factor = self.typed_config.speed_factor
        self.streaming_mode = self.typed_config.streaming_mode
        self.output_device_name = self.typed_config.output_device_name
        self.sample_rate = self.typed_config.sample_rate
        self.channels = 1
        self.dtype = np.int16

        self.use_text_cleanup = self.typed_config.use_text_cleanup
        self.use_vts_lip_sync = self.typed_config.use_vts_lip_sync
        self.use_subtitle = self.typed_config.use_subtitle

        self.input_pcm_queue = deque(b"")
        self.audio_data_queue = deque(maxlen=1000)

        self.render_count = 0
        self.error_count = 0
        self.sequence_count = 0
        self.audio_manager: Any = None
        self._is_connected = False
        self._has_started = False
        self.tts_lock = asyncio.Lock()

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="omni_tts_synthesize",
                description="Omni TTS 语音合成（保留已知 KeyError 现状，不补文本清洗）",
                kind="sync",
                provider="builtin",
                parameters_schema=_OMNI_TTS_SCHEMA,
            ),
            ToolSpec(
                name="omni_tts_get_stats",
                description="读取 Omni TTS 状态统计",
                kind="sync",
                provider="builtin",
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "omni_tts_synthesize":
                text = str(args.get("text", ""))
                if not text:
                    return _fail(n, "缺少 text 字段")
                await self.handle_speech(text)
                return _ok(n, True)
            if n == "omni_tts_get_stats":
                return _ok(n, True, self.get_stats())
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"Omni TTS 工具 {invocation.tool_name} 调用异常: {exc}", exc_info=True)
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            return
        if not TTS_DEPENDENCIES_OK:
            raise ImportError("TTS dependencies not available")

        from src.modules.tts import AudioDeviceManager

        manager = AudioDeviceManager(
            sample_rate=self.sample_rate,
            channels=self.channels,
            dtype=self.dtype,
        )
        if self.output_device_name:
            from src.modules.tools.output.tts.device_finder import find_device_index

            index = find_device_index(
                device_name=self.output_device_name,
                logger=self.logger,
                sample_rate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
            )
            if index is not None:
                manager.set_output_device(device_index=index)
        self.audio_manager = manager

        self._has_started = True
        self._is_connected = True
        self.logger.info("OmniTTSProvider 设置完成")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        self.logger.info("OmniTTSProvider 清理中...")
        if self.audio_manager:
            self.audio_manager.stop_audio()
        self.input_pcm_queue.clear()
        self.audio_data_queue.clear()
        self._is_connected = False
        self._has_started = False
        self.logger.info("OmniTTSProvider 清理完成")

    # ===== 业务方法 =====

    async def handle_speech(self, text: str) -> None:
        """TTS 播放入口"""
        async with self.tts_lock:
            await self._synthesize(text)

    async def _synthesize(self, text: str) -> None:
        """合成并缓冲（不补文本清洗，保留已知 bug）"""
        self.sequence_count = 0
        try:
            audio_stream = self._tts_stream(text)
            for chunk in audio_stream:
                if not chunk:
                    continue
                await self._decode_and_buffer(chunk)
        except Exception as e:
            self.logger.error(f"TTS 播放失败: {e}")
            raise

    def _tts_stream(self, text: str):
        """HTTP 流式 TTS 请求"""
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
            raise Exception(f"TTS API 错误: {error_msg}")

        return response.iter_content(chunk_size=4096)

    async def _decode_and_buffer(self, wav_chunk) -> None:
        """解码 WAV 并缓冲（保留已知 KeyError bug）"""
        from src.modules.tools.output.tts.wav_decoder import extract_pcm_from_wav

        try:
            if isinstance(wav_chunk, str):
                wav_data = base64.b64decode(wav_chunk)
            else:
                wav_data = wav_chunk

            pcm_data = extract_pcm_from_wav(wav_data)
            self.input_pcm_queue.extend(pcm_data)

            block_size = 1024 * self.channels * self.dtype().itemsize
            while len(self.input_pcm_queue) >= block_size:
                raw_block = b""
                for _ in range(block_size):
                    raw_block += bytes([self.input_pcm_queue.popleft()])

                self.sequence_count += 1
                self.audio_data_queue.append(raw_block)
        except Exception as e:
            self.logger.error(f"解码音频数据失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": True,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "buffer_size": len(self.input_pcm_queue),
        }


def _ok(tool_name: str, success: bool, structured: Any = None) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=bool(success),
        structured_content=structured,
        content="" if structured is None else str(structured),
    )


def _fail(tool_name: str, error_message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error_message=error_message,
    )


def create_omni_tts_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> OmniTTSProvider:
    return OmniTTSProvider(
        config=config,
        event_bus=event_bus,
    )


def register_omni_tts_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> OmniTTSProvider:
    provider = create_omni_tts_provider(
        config=config,
        event_bus=event_bus,
    )
    if hasattr(registry, "register_provider"):
        registry.register_provider(provider)
    else:
        for spec in provider.list_tools():
            registry.register(spec, provider.invoke)
    return provider
