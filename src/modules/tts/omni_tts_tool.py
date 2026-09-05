"""OmniTTSProvider - Omni TTS 语音合成引擎

- 引擎：GPT-SoVITS HTTP 流式请求 + wav 解码 + AudioDeviceManager 播放
- 调用方：``src.agents.streamer.utterance_queue``（编排层确定性调用）
- 自治生命周期：``handle_speech()`` 入口自动 ensure_setup，调用方无需预调
- 错误语义：合成失败主动发 ``tts.utterance.failed`` 并 raise，由队列兜底
- 接受可选 ``utterance_id`` 参数：传入时按 ``tts.utterance.*`` 生命周期发布
  事件；缺省/None 时静默跳过（手动 / 直调场景无 utterance 上下文）
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from .common import (
    build_stats_dict,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
)

if TYPE_CHECKING:
    pass


# 检查依赖
TTS_DEPENDENCIES_OK = False
try:
    import requests

    TTS_DEPENDENCIES_OK = True
except ImportError:
    pass


class OmniTTSProvider:
    """Omni TTS 语音合成引擎。

    引擎通过 ``handle_speech(text, utterance_id)`` 暴露唯一入口；调用方
    （编排队列）无需关心 setup / cleanup 生命周期——``handle_speech`` 开头
    自动 ensure_setup。
    """

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
        sample_rate: int = Field(default=32000, ge=8000, le=48000, description="采样率")
        use_text_cleanup: bool = Field(default=True, description="是否使用文本清理")

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
        self.sample_rate = self.typed_config.sample_rate
        self.channels = 1
        self.dtype = np.int16

        self.use_text_cleanup = self.typed_config.use_text_cleanup

        self.render_count = 0
        self.error_count = 0
        self.sequence_count = 0
        self.audio_manager: Any = None
        self._is_connected = False
        self._has_started = False
        self.tts_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    # ===== 生命周期 =====

    async def setup(self) -> None:
        """初始化引擎（幂等）。"""
        if self._has_started:
            return
        if not TTS_DEPENDENCIES_OK:
            raise ImportError("TTS dependencies not available")

        from src.modules.audio import AudioDeviceManager

        manager = AudioDeviceManager(
            sample_rate=self.sample_rate,
            channels=self.channels,
            dtype=self.dtype,
        )
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
        self._is_connected = False
        self._has_started = False
        self.logger.info("OmniTTSProvider 清理完成")

    # ===== 业务方法 =====

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        """TTS 播放入口。

        Args:
            text: 要合成的文本。
            utterance_id: 一次发声实例的唯一 ID；非 None 时按
                ``tts.utterance.*`` 生命周期发布事件。
        """
        if not self._has_started:
            await self.setup()
        async with self.tts_lock:
            await self._synthesize(text, utterance_id=utterance_id)

    async def _synthesize(self, text: str, utterance_id: Optional[str] = None) -> None:
        """HTTP 流式拉取 → 解码 → AudioDeviceManager 流式播放

        之前的实现只把解码缓冲到 ``audio_data_queue``，从未真正出声；本次改造
        按 GPT-SoVITS 的流式播放模式重写：start_stream → 每块 write_chunk →
        stop_stream。started 在首块 PCM 写声卡前发布（流式引擎合成未完，
        duration_ms=None）；finished 在 stop_stream 后发布。
        """
        self.sequence_count = 0

        # 流式引擎合成未完 → started payload duration_ms 传 None
        await emit_utterance_started(
            self.event_bus,
            utterance_id=utterance_id,
            speech_text=text,
            engine=self.PROVIDER_NAME,
            duration_ms=None,
        )

        try:
            audio_stream = self._tts_stream(text)
            self.audio_manager.start_stream()

            for chunk in audio_stream:
                if not chunk:
                    continue
                pcm_array = self._decode_to_pcm(chunk)
                if pcm_array is None or len(pcm_array) == 0:
                    continue
                self.audio_manager.write_chunk(pcm_array)
                self.sequence_count += 1

            self.audio_manager.stop_stream()
            self.render_count += 1

            await emit_utterance_finished(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                duration_ms=0,
            )
        except asyncio.CancelledError:
            # 协程取消不会自动关闭声卡流，超时取消时必须显式截断
            if self.audio_manager:
                self.audio_manager.stop_stream()
            self.logger.warning("TTS 渲染被取消（已截断播放）: utterance_id=: {}", utterance_id)
            raise
        except Exception as e:
            self.error_count += 1
            self.logger.error("TTS 播放失败: : {}", e)
            if self.audio_manager:
                self.audio_manager.stop_stream()
            await emit_utterance_failed(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                error_message=f"{type(e).__name__}: {e}",
            )
            raise

    def _tts_stream(self, text: str):
        """HTTP 流式 TTS 请求"""
        params = {
            "text": text,
            "text_lang": self.text_language,
        }
        # 参考音频三键仅在已设置时携带；省略时由服务端使用其启动配置的
        # 默认参考音频（与 GPT-SoVITS API 同源的约定）
        if self.ref_audio_path:
            params["ref_audio_path"] = self.ref_audio_path
            params["prompt_text"] = self.prompt_text
            params["prompt_lang"] = self.prompt_language
        params["top_k"] = self.top_k
        params["top_p"] = self.top_p
        params["temperature"] = self.temperature
        params["speed_factor"] = self.speed_factor
        params["streaming_mode"] = True
        params["media_type"] = "wav"

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

    def _decode_to_pcm(self, wav_chunk) -> Optional[np.ndarray]:
        """解码 WAV 块为 int16 numpy 数组，失败返回 None。

        之前 ``_decode_and_buffer`` 把 PCM 缓冲到 ``input_pcm_queue`` 后手工切片
        1024 字节块再追加到 ``audio_data_queue``；现在直接整块解码交给
        AudioDeviceManager 流式播放，避免保留历史 KeyError bug 的拼接逻辑。
        """
        # 函数体内导入：单元测试 patch wav_decoder.extract_pcm_from_wav 拦截
        # 解码，顶部导入会使 patch 失效
        from src.modules.tts.wav_decoder import extract_pcm_from_wav

        try:
            if isinstance(wav_chunk, str):
                wav_data = base64.b64decode(wav_chunk)
            else:
                wav_data = wav_chunk

            pcm_data = extract_pcm_from_wav(wav_data)
            if not pcm_data:
                return None
            return np.frombuffer(pcm_data, dtype=self.dtype)
        except Exception as e:
            self.logger.warning("解码音频块失败，跳过该块: : {}", e)
            return None

    def get_stats(self) -> Dict[str, Any]:
        return build_stats_dict(
            name=self.__class__.__name__,
            is_connected=self._is_connected,
            render_count=self.render_count,
            error_count=self.error_count,
        )


def create_omni_tts_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> OmniTTSProvider:
    """工厂：构造 ``OmniTTSProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``。
    """
    return OmniTTSProvider(
        config=config,
        event_bus=event_bus,
    )


__all__ = ["OmniTTSProvider", "create_omni_tts_provider"]
