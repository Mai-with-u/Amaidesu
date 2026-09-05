"""GPTSoVITSProvider - GPT-SoVITS 语音合成引擎

- 引擎：GPT-SoVITS 客户端初始化 + 流式合成 + AudioDeviceManager 播放
- 调用方：``src.agents.streamer.utterance_queue``（编排层确定性调用）
- 自治生命周期：``handle_speech()`` 入口自动 ensure_setup，调用方无需预调
- 错误语义：合成失败主动发 ``tts.utterance.failed`` 并 raise，由队列兜底
- 接受可选 ``utterance_id`` 参数：传入时按 ``tts.utterance.*`` 生命周期发布
  事件；缺省/None 时静默跳过（手动 / 直调场景无 utterance 上下文）
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
from .common import (
    build_stats_dict,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
)
from .gptsovits_client import GPTSoVITSClient

if TYPE_CHECKING:
    pass


# 音频流参数
CHANNELS = 1
DTYPE = np.int16
BLOCKSIZE = 1024
SAMPLE_SIZE = DTYPE().itemsize
BUFFER_REQUIRED_BYTES = BLOCKSIZE * CHANNELS * SAMPLE_SIZE


# GPT-SoVITS 中文归一化器文本清洗
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
    """白名单字符集规避 KeyError bug"""
    return _TTS_UNSAFE_CHARS.sub("", text)


class GPTSoVITSProvider:
    """GPT-SoVITS 语音合成引擎。

    引擎通过 ``handle_speech(text, utterance_id)`` 暴露唯一入口；调用方
    （编排队列）无需关心 setup / cleanup 生命周期——``handle_speech`` 开头
    自动 ensure_setup。
    """

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

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    # ===== 生命周期 =====

    async def setup(self) -> None:
        """初始化引擎（幂等）。"""
        if self._has_started:
            return
        self.tts_client = GPTSoVITSClient(self.host, self.port)
        self.tts_client.initialize()

        if self.ref_audio_path and self.prompt_text:
            self.tts_client.set_refer_audio(self.ref_audio_path, self.prompt_text)

        from src.modules.audio import AudioDeviceManager

        manager = AudioDeviceManager(
            sample_rate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
        )
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

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        """合成并播放语音。

        Args:
            text: 要合成的文本。
            utterance_id: 一次发声实例的唯一 ID；非 None 时按
                ``tts.utterance.*`` 生命周期发布事件。
        """
        if not self._has_started:
            await self.setup()
        if not text or not text.strip():
            self.logger.debug("TTS 文本为空，跳过渲染")
            return

        original_text = text.strip()
        self.logger.debug(f"准备 TTS: '{original_text[:50]}...'")

        # 文本清洗
        final_text = _sanitize_text_for_tts(original_text)
        if final_text != original_text:
            self.logger.debug(f"文本清洗: 移除了 {len(original_text) - len(final_text)} 个不支持字符")
        if not final_text.strip():
            self.logger.debug("清洗后文本为空，跳过渲染")
            return

        # 流式引擎合成未完时 duration_ms 传 None；started 在首块 PCM 写声卡前发布
        await emit_utterance_started(
            self.event_bus,
            utterance_id=utterance_id,
            speech_text=final_text,
            engine=self.PROVIDER_NAME,
            duration_ms=None,
        )

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

            # 流式引擎 finished 时长按整段文本播完估时（精确分块时长需声卡反馈，YAGNI 不做）
            await emit_utterance_finished(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                duration_ms=0,
            )

        except asyncio.CancelledError:
            # 队列看门狗超时取消：声卡流必须显式关闭，否则已写入的
            # PCM 会继续播出，与下一条 utterance 的音频重叠
            self.audio_manager.stop_stream()
            self.logger.warning("TTS 渲染被取消（已截断播放）: utterance_id=: {}", utterance_id)
            raise
        except Exception as e:
            self.error_count += 1
            self.logger.opt(exception=True).error("TTS 渲染失败: : {}", e)
            await emit_utterance_failed(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                error_message=f"{type(e).__name__}: {e}",
            )
            raise

    async def _process_audio_stream(self, audio_stream):
        """处理音频流（同步/异步迭代器）"""
        # 函数体内导入：单元测试 patch wav_decoder.decode_wav_chunk 拦截解码，
        # 顶部导入会使 patch 失效
        from src.modules.tts.wav_decoder import decode_wav_chunk

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
            self.logger.opt(exception=True).error("处理音频流失败: : {}", e)
            raise

    def get_stats(self) -> Dict[str, Any]:
        return build_stats_dict(
            name=self.__class__.__name__,
            is_connected=self._is_connected,
            render_count=self.render_count,
            error_count=self.error_count,
        )


def create_gptsovits_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> GPTSoVITSProvider:
    """工厂：构造 ``GPTSoVITSProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``。
    """
    return GPTSoVITSProvider(
        config=config,
        event_bus=event_bus,
    )


__all__ = ["GPTSoVITSProvider", "create_gptsovits_provider"]
