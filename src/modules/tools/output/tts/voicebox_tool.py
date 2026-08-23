"""
VoiceboxProvider - Voicebox 语音合成工具（Wave 4 / §1.5）

迁移自 ``src.stages.output.handlers.audio.voicebox.VoiceboxHandler``：

- 引擎 verbatim（Voicebox HTTP API: POST /generate → SSE 轮询 → GET /audio）
- ``AudioHandlerBase`` 继承被去除；ToolProvider 协议由本类自身实现
- 工具 ``voicebox_synthesize(text)`` 调用 ``handle_speech()`` 入口
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, Optional

import aiohttp
import numpy as np
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.streaming.audio_stream_channel import AudioStreamChannel
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec

if TYPE_CHECKING:
    pass


CHANNELS = 1
DTYPE = np.int16
BLOCKSIZE = 1024


_VOICEBOX_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "要合成的文本"},
    },
    "required": ["text"],
}


class VoiceboxProvider:
    """Voicebox ToolProvider"""

    PROVIDER_NAME = "voicebox"

    class ConfigSchema(BaseConfig):
        type: str = "voicebox"
        host: str = Field(default="127.0.0.1", description="Voicebox API 主机地址")
        port: int = Field(default=17493, ge=1, le=65535, description="Voicebox API 端口")
        profile_id: Optional[str] = Field(default=None, description="语音 profile ID")
        language: str = Field(default="zh", description="语言代码")
        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")
        sample_rate: int = Field(default=24000, ge=8000, le=48000, description="输出采样率")
        generation_timeout_ms: int = Field(default=120000, ge=5000, le=300000, description="生成超时（毫秒）")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        audio_stream_channel: Optional[AudioStreamChannel] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.audio_stream_channel = audio_stream_channel
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(config)
        self.host = self.typed_config.host
        self.port = self.typed_config.port
        self.base_url = f"http://{self.host}:{self.port}"
        self.profile_id = self.typed_config.profile_id
        self.language = self.typed_config.language
        self.sample_rate = self.typed_config.sample_rate
        self.generation_timeout = self.typed_config.generation_timeout_ms / 1000

        self.render_count = 0
        self.error_count = 0
        self._session: Optional[aiohttp.ClientSession] = None
        self.audio_manager: Any = None
        self._is_connected = False
        self._has_started = False
        self.tts_lock: Optional[asyncio.Lock] = None

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="voicebox_synthesize",
                description="Voicebox 语音合成（POST /generate → SSE → GET /audio）",
                kind="sync",
                provider="builtin",
                parameters_schema=_VOICEBOX_SCHEMA,
            ),
            ToolSpec(
                name="voicebox_get_stats",
                description="读取 Voicebox 状态统计",
                kind="sync",
                provider="builtin",
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "voicebox_synthesize":
                text = str(args.get("text", ""))
                if not text:
                    return _fail(n, "缺少 text 字段")
                await self.handle_speech(text)
                return _ok(n, True)
            if n == "voicebox_get_stats":
                return _ok(n, True, self.get_stats())
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"Voicebox 工具 {invocation.tool_name} 调用异常: {exc}", exc_info=True)
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    async def setup(self) -> None:
        if self._has_started:
            return

        if self.tts_lock is None:
            self.tts_lock = asyncio.Lock()

        self._session = aiohttp.ClientSession()

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

        try:
            async with self._session.get(f"{self.base_url}/health", timeout=aiohttp.ClientTimeout(total=5)):
                self.logger.info("Voicebox 服务连接成功")
        except Exception as e:
            self.logger.warning(f"Voicebox 健康检查失败: {e}")

        self._has_started = True
        self._is_connected = True
        self.logger.info("VoiceboxProvider 设置完成")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        self.logger.info("VoiceboxProvider 清理中...")
        if self.audio_manager:
            self.audio_manager.stop_audio()
        if self._session and not self._session.closed:
            await self._session.close()
        self._is_connected = False
        self._has_started = False
        self.logger.info("VoiceboxProvider 清理完成")

    async def handle_speech(self, text: str) -> None:
        """对应父类模板方法 handle()（verbatim）"""
        if self.tts_lock is None:
            self.tts_lock = asyncio.Lock()
        async with self.tts_lock:
            await self._notify_audio_start(text)
            try:
                await self._synthesize(text)
            finally:
                await self._notify_audio_end(text)

    async def _synthesize(self, text: str) -> None:
        """verbatim _synthesize"""
        if not self._session or self._session.closed:
            raise RuntimeError("Voicebox HTTP 会话未初始化")
        if not self.profile_id:
            raise RuntimeError("未配置 profile_id，请先在 Voicebox 中克隆音色并填写配置")

        generation_id = await self._create_generation(text)
        await self._wait_for_completion(generation_id)
        audio_data = await self._download_audio(generation_id)

        from src.modules.tools.output.tts.wav_decoder import extract_pcm_from_wav

        pcm_data = extract_pcm_from_wav(audio_data)
        audio_array = np.frombuffer(pcm_data, dtype=DTYPE)

        if len(audio_array) == 0:
            self.logger.warning("Voicebox 返回空音频，跳过播放")
            return

        chunk_size = BLOCKSIZE * CHANNELS
        chunk_index = 0
        for i in range(0, len(audio_array), chunk_size):
            chunk_data = audio_array[i : i + chunk_size]
            await self._publish_chunk(
                data=chunk_data.tobytes(),
                sample_rate=self.sample_rate,
                channels=CHANNELS,
                sequence=chunk_index,
            )
            chunk_index += 1

        await self.audio_manager.play_audio(audio_array)

    async def _create_generation(self, text: str) -> str:
        """verbatim POST /generate"""
        if not self._session or self._session.closed:
            raise RuntimeError("Voicebox HTTP 会话未初始化")
        payload = {
            "text": text,
            "profile_id": self.profile_id,
            "language": self.language,
        }

        session = self._session
        async with session.post(
            f"{self.base_url}/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Voicebox /generate 失败 (HTTP {resp.status}): {body[:200]}")
            data = await resp.json()
            gen_id = data.get("id")
            if not gen_id:
                raise RuntimeError(f"Voicebox /generate 返回中没有 id: {data}")
            self.logger.debug(f"创建生成任务: id={gen_id}")
            return gen_id

    async def _wait_for_completion(self, generation_id: str) -> None:
        """verbatim SSE 轮询"""
        if not self._session or self._session.closed:
            raise RuntimeError("Voicebox HTTP 会话未初始化")
        url = f"{self.base_url}/generate/{generation_id}/status"
        timeout = aiohttp.ClientTimeout(total=self.generation_timeout + 10)
        session = self._session

        async with session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Voicebox 状态轮询失败 (HTTP {resp.status}): {body[:200]}")
            async for line_bytes in resp.content:
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                event_data = line[len("data: ") :]
                try:
                    event = json.loads(event_data)
                except json.JSONDecodeError:
                    continue
                status = event.get("status")
                if status == "completed":
                    duration = event.get("duration", 0)
                    self.logger.debug(f"生成完成: id={generation_id}, 耗时={duration}s")
                    return
                if status == "failed":
                    err = event.get("error", "未知错误")
                    raise RuntimeError(f"Voicebox 生成失败: {err}")

    async def _download_audio(self, generation_id: str) -> bytes:
        """verbatim GET /audio"""
        if not self._session or self._session.closed:
            raise RuntimeError("Voicebox HTTP 会话未初始化")
        url = f"{self.base_url}/audio/{generation_id}"
        session = self._session
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Voicebox 音频下载失败 (HTTP {resp.status}): {body[:200]}")
            data = await resp.read()
            self.logger.debug(f"下载音频: id={generation_id}, 大小={len(data)} 字节")
            return data

    async def _notify_audio_start(self, text: str) -> None:
        if self.audio_stream_channel:
            from src.modules.streaming.audio_chunk import AudioMetadata

            await self.audio_stream_channel.notify_start(AudioMetadata(text=text, sample_rate=0, channels=0))

    async def _notify_audio_end(self, text: str) -> None:
        if self.audio_stream_channel:
            from src.modules.streaming.audio_chunk import AudioMetadata

            await self.audio_stream_channel.notify_end(AudioMetadata(text=text, sample_rate=0, channels=0))

    async def _publish_chunk(self, data: bytes, sample_rate: int, channels: int, sequence: int) -> None:
        if self.audio_stream_channel:
            import time as _time

            from src.modules.streaming.audio_chunk import AudioChunk

            await self.audio_stream_channel.publish(
                AudioChunk(
                    data=data,
                    sample_rate=sample_rate,
                    channels=channels,
                    sequence=sequence,
                    timestamp=_time.time(),
                )
            )

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._session is not None and not self._session.closed,
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


def create_voicebox_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    audio_stream_channel: Optional[AudioStreamChannel] = None,
) -> VoiceboxProvider:
    return VoiceboxProvider(
        config=config,
        event_bus=event_bus,
        audio_stream_channel=audio_stream_channel,
    )


def register_voicebox_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    audio_stream_channel: Optional[AudioStreamChannel] = None,
) -> VoiceboxProvider:
    provider = create_voicebox_provider(
        config=config,
        event_bus=event_bus,
        audio_stream_channel=audio_stream_channel,
    )
    if hasattr(registry, "register_provider"):
        registry.register_provider(provider)
    else:
        for spec in provider.list_tools():
            registry.register(spec, provider.invoke)
    return provider
