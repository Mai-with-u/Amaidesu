"""
GPTSoVITSProvider - GPT-SoVITS 语音合成工具（Wave 4 / §1.5）

迁移自 ``src.stages.output.handlers.audio.gptsovits.GPTSoVITSHandler``：

- 引擎 verbatim（GPT-SoVITS 客户端初始化 + 流式合成 + AudioDeviceManager 播放）
- ``AudioHandlerBase`` 继承被去除；ToolProvider 协议由本类自身实现
- 文本清洗（``_sanitize_text_for_tts``）verbatim 保留
- 工具 ``gptsovits_synthesize(text)`` 调用 ``handle_speech()`` 入口
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tts import GPTSoVITSClient

if TYPE_CHECKING:
    pass


# 音频流参数（verbatim）
CHANNELS = 1
DTYPE = np.int16
BLOCKSIZE = 1024
SAMPLE_SIZE = DTYPE().itemsize
BUFFER_REQUIRED_BYTES = BLOCKSIZE * CHANNELS * SAMPLE_SIZE


# GPT-SoVITS 中文归一化器文本清洗（verbatim）
_TTS_UNSAFE_CHARS = re.compile(
    r"[^\u4e00-\u9fff"
    r"\u3000-\u303f"
    r"\uff01-\uff0f"
    r"\uff1a-\uff20"
    r"\uff3b-\uff40"
    r"\uff5b-\uff5e"
    r"a-zA-Z0-9\s"
    r"!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~]"
)


def _sanitize_text_for_tts(text: str) -> str:
    """白名单清洗（verbatim，规避 GPT-SoVITS KeyError bug）"""
    return _TTS_UNSAFE_CHARS.sub("", text)


_GPTSOVITS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "要合成的文本"},
    },
    "required": ["text"],
}


class GPTSoVITSProvider:
    """GPT-SoVITS ToolProvider"""

    PROVIDER_NAME = "gptsovits"

    class ConfigSchema(BaseConfig):
        """GPT-SoVITS 配置"""

        type: str = "gptsovits"
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
        media_type: str = Field(default="wav", pattern=r"^(wav|mp3|ogg)$", description="媒体类型")
        text_split_method: str = Field(
            default="cut5",
            pattern=r"^(cut0|cut1|cut2|cut3|cut4|cut5)$",
            description="文本分割方法",
        )
        batch_size: int = Field(default=1, ge=1, le=10, description="批处理大小")
        batch_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="批处理阈值")
        repetition_penalty: float = Field(default=1.0, ge=0.5, le=2.0, description="重复惩罚")
        sample_steps: int = Field(default=10, ge=1, le=50, description="采样步数")
        super_sampling: bool = Field(default=True, description="是否启用超采样")
        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")
        sample_rate: int = Field(default=32000, ge=8000, le=48000, description="采样率")

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
        self.ref_audio_path = self.typed_config.ref_audio_path
        self.prompt_text = self.typed_config.prompt_text
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
        self.sample_rate = self.typed_config.sample_rate

        self.input_pcm_queue = deque(b"")
        self.audio_data_queue = deque(maxlen=1000)

        self.render_count = 0
        self.error_count = 0
        self.tts_client: Optional[GPTSoVITSClient] = None
        self.audio_manager: Any = None
        self._is_connected = False
        self._has_started = False
        # 串行化锁
        self.tts_lock = asyncio.Lock()

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="gptsovits_synthesize",
                description="GPT-SoVITS 语音合成（接收 text 字段，含白名单清洗）",
                kind="sync",
                provider="builtin",
                parameters_schema=_GPTSOVITS_SCHEMA,
            ),
            ToolSpec(
                name="gptsovits_get_stats",
                description="读取 GPT-SoVITS 状态统计",
                kind="sync",
                provider="builtin",
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "gptsovits_synthesize":
                text = str(args.get("text", ""))
                if not text:
                    return _fail(n, "缺少 text 字段")
                await self.handle_speech(text)
                return _ok(n, True)
            if n == "gptsovits_get_stats":
                return _ok(n, True, self.get_stats())
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"GPT-SoVITS 工具 {invocation.tool_name} 调用异常: {exc}", exc_info=True)
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            return
        self.tts_client = GPTSoVITSClient(self.host, self.port)
        self.tts_client.initialize()

        if self.ref_audio_path and self.prompt_text:
            self.tts_client.set_refer_audio(self.ref_audio_path, self.prompt_text)

        from src.modules.tts import AudioDeviceManager

        manager = AudioDeviceManager(
            sample_rate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
        )
        if self.typed_config.output_device_name:
            from src.modules.tools.output.tts.device_finder import find_device_index

            index = find_device_index(
                device_name=self.typed_config.output_device_name,
                logger=self.logger,
                sample_rate=self.sample_rate,
                channels=CHANNELS,
                dtype=DTYPE,
            )
            if index is not None:
                manager.set_output_device(device_index=index)
        self.audio_manager = manager

        self.tts_client.load_preset("default")
        self._has_started = True
        self._is_connected = True
        self.logger.info("GPTSoVITSProvider 设置完成")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        self.logger.info("GPTSoVITSProvider 清理中...")
        if self.audio_manager:
            self.audio_manager.stop_stream()
        self.input_pcm_queue.clear()
        self.audio_data_queue.clear()
        self._is_connected = False
        self._has_started = False
        self.logger.info("GPTSoVITSProvider 清理完成")

    # ===== 业务方法 =====

    async def handle_speech(self, text: str) -> None:
        """对应旧 GPTSoVITSHandler.handle()（覆盖父类 handle 因为需要文本清洗）"""
        if not text or not text.strip():
            self.logger.debug("TTS 文本为空，跳过渲染")
            return

        original_text = text.strip()
        self.logger.debug(f"准备 TTS: '{original_text[:50]}...'")

        # 文本清洗（verbatim）
        final_text = _sanitize_text_for_tts(original_text)
        if final_text != original_text:
            self.logger.debug(f"文本清洗: 移除了 {len(original_text) - len(final_text)} 个不支持字符")
        if not final_text.strip():
            self.logger.debug("清洗后文本为空，跳过渲染")
            return

        try:
            async with self.tts_lock:
                audio_stream = self.tts_client.tts_stream(  # type: ignore[union-attr]
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

                self.audio_manager.start_stream()

                chunk_index = 0
                async for chunk in self._process_audio_stream(audio_stream):
                    if chunk is not None:
                        self.audio_manager.write_chunk(chunk)
                        chunk_index += 1

                self.audio_manager.stop_stream()

            self.logger.debug(f"TTS 播放完成: '{final_text[:30]}...'")
            self.render_count += 1

        except Exception as e:
            self.logger.error(f"TTS 渲染失败: {e}", exc_info=True)
            self.error_count += 1
            raise

    async def _process_audio_stream(self, audio_stream):
        """verbatim 处理音频流（同步/异步迭代器）"""
        from src.modules.tools.output.tts.wav_decoder import decode_wav_chunk

        try:
            if hasattr(audio_stream, "__aiter__"):
                async for chunk in audio_stream:
                    if not chunk:
                        continue
                    audio_chunk = await decode_wav_chunk(chunk, dtype=DTYPE)
                    if audio_chunk is not None:
                        yield audio_chunk
            else:
                for chunk in audio_stream:
                    if not chunk:
                        continue
                    audio_chunk = await decode_wav_chunk(chunk, dtype=DTYPE)
                    if audio_chunk is not None:
                        yield audio_chunk
        except Exception as e:
            self.logger.error(f"处理音频流失败: {e}", exc_info=True)
            raise

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
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


def create_gptsovits_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> GPTSoVITSProvider:
    return GPTSoVITSProvider(
        config=config,
        event_bus=event_bus,
    )


def register_gptsovits_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> GPTSoVITSProvider:
    provider = create_gptsovits_provider(
        config=config,
        event_bus=event_bus,
    )
    if hasattr(registry, "register_provider"):
        registry.register_provider(provider)
    else:
        for spec in provider.list_tools():
            registry.register(spec, provider.invoke)
    return provider
