"""
EdgeTTSProvider - Edge TTS 语音合成工具（Wave 4 / §1.5）

迁移自 ``src.stages.output.handlers.audio.edge_tts.EdgeTTSHandler``：

- 引擎 verbatim（Edge TTS 临时文件合成 + soundfile 读取 + AudioDeviceManager 播放）
- ``AudioHandlerBase`` 继承被去除；ToolProvider 协议由本类自身实现
- 工具 ``edge_tts_synthesize(text)`` 调用 ``handle_speech()`` 入口
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.streaming.audio_stream_channel import AudioStreamChannel
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec

if TYPE_CHECKING:
    pass


DEPENDENCIES_OK = True
try:
    import soundfile as sf
except ImportError:
    DEPENDENCIES_OK = False

try:
    import edge_tts
except ImportError:
    pass  # edge_tts 可选，由配置决定使用哪个引擎


_EDGE_TTS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "要合成的文本"},
    },
    "required": ["text"],
}


class EdgeTTSProvider:
    """Edge TTS ToolProvider"""

    PROVIDER_NAME = "edge_tts"

    class ConfigSchema(BaseConfig):
        """EdgeTTS 配置"""

        type: str = "edge_tts"
        voice: str = Field(default="zh-CN-XiaoxiaoNeural", description="Edge TTS 语音")
        output_device_name: Optional[str] = Field(default=None, description="音频输出设备名称")

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
        self.voice = self.typed_config.voice
        self.output_device_name = self.typed_config.output_device_name or ""

        # 音频设备管理器（在 setup 中初始化）
        self.audio_manager: Any = None
        self._is_connected = False
        self._has_started = False
        # 串行化锁：同一时间只合成一句话
        self.tts_lock = asyncio.Lock()
        self.render_count = 0
        self.error_count = 0

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="edge_tts_synthesize",
                description="Edge TTS 语音合成（接收 text 字段）",
                kind="sync",
                provider="builtin",
                parameters_schema=_EDGE_TTS_SCHEMA,
            ),
            ToolSpec(
                name="edge_tts_get_stats",
                description="读取 Edge TTS 状态统计",
                kind="sync",
                provider="builtin",
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "edge_tts_synthesize":
                text = str(args.get("text", ""))
                if not text:
                    return _fail(n, "缺少 text 字段")
                await self.handle_speech(text)
                return _ok(n, True)
            if n == "edge_tts_get_stats":
                return _ok(n, True, self.get_stats())
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"Edge TTS 工具 {invocation.tool_name} 调用异常: {exc}", exc_info=True)
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            return
        if not DEPENDENCIES_OK:
            raise RuntimeError("缺少必要的依赖: soundfile")
        if "edge_tts" not in globals():
            raise RuntimeError("Edge TTS 引擎未安装")

        self._setup_audio_device()
        self._has_started = True
        self._is_connected = True
        self.logger.info("EdgeTTSProvider 启动完成")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        self.logger.info("EdgeTTSProvider 停止中...")
        if self.audio_manager:
            self.audio_manager.stop_audio()
        self._is_connected = False
        self._has_started = False
        self.logger.info("EdgeTTSProvider 停止完成")

    # ===== 业务方法 =====

    async def handle_speech(self, text: str) -> None:
        """合成并播放语音（对应旧 EdgeTTSHandler.handle()）"""
        if not text:
            self.logger.debug("TTS 文本为空，跳过渲染")
            return
        async with self.tts_lock:
            await self._notify_audio_start(text)
            try:
                await self._synthesize(text)
            finally:
                await self._notify_audio_end(text)

    async def _synthesize(self, text: str) -> None:
        """执行 TTS 合成 + publish + 播放（对应父类 handle() 模板）"""
        self.logger.debug(f"开始 TTS 渲染: '{text[:30]}...'")
        try:
            audio_array, samplerate = await self._edge_tts_synthesize(text)
            duration_seconds = len(audio_array) / samplerate if samplerate > 0 else 3.0
            self.logger.debug(f"音频时长: {duration_seconds:.3f}秒")

            chunk_size = 1024
            for i in range(0, len(audio_array), chunk_size):
                chunk_data = audio_array[i : i + chunk_size]
                await self._publish_chunk(
                    data=chunk_data.tobytes(),
                    sample_rate=samplerate,
                    channels=1,
                    sequence=i // chunk_size,
                )

            self.logger.debug("开始播放音频...")
            await self.audio_manager.play_audio(audio_array)
        except Exception as e:
            self.logger.error(f"TTS 渲染失败: {e}", exc_info=True)
            raise RuntimeError(f"TTS 渲染失败: {e}") from e

    async def _edge_tts_synthesize(self, text: str):
        """调用 Edge TTS 合成（verbatim）"""
        if "edge_tts" not in globals():
            raise RuntimeError("Edge TTS 未安装")
        tmp_filename = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                tmp_filename = tmp_file.name
            communicate = edge_tts.Communicate(text, self.voice)
            await asyncio.to_thread(communicate.save_sync, tmp_filename)
            self.logger.debug(f"Edge TTS 合成完成: {tmp_filename}")
            audio_array, samplerate = await asyncio.to_thread(sf.read, tmp_filename, dtype="float32")
            return audio_array, samplerate
        except Exception as e:
            self.logger.error(f"Edge TTS 合成失败: {e}", exc_info=True)
            return np.zeros(44100, dtype=np.float32), 16000
        finally:
            if tmp_filename:
                try:
                    os.remove(tmp_filename)
                except Exception:
                    pass

    def _setup_audio_device(self) -> None:
        """初始化音频设备管理器（verbatim）"""
        from src.modules.tts import AudioDeviceManager

        manager = AudioDeviceManager(sample_rate=48000, channels=1, dtype=np.float32)
        if self.output_device_name:
            from src.modules.tools.output.tts.device_finder import find_device_index

            index = find_device_index(
                device_name=self.output_device_name,
                logger=self.logger,
                sample_rate=48000,
                channels=1,
                dtype=np.float32,
            )
            if index is not None:
                manager.set_output_device(device_index=index)
        self.audio_manager = manager

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


def create_edge_tts_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    audio_stream_channel: Optional[AudioStreamChannel] = None,
) -> EdgeTTSProvider:
    return EdgeTTSProvider(
        config=config,
        event_bus=event_bus,
        audio_stream_channel=audio_stream_channel,
    )


def register_edge_tts_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    audio_stream_channel: Optional[AudioStreamChannel] = None,
) -> EdgeTTSProvider:
    provider = create_edge_tts_provider(
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
