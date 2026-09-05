"""VoiceboxProvider - Voicebox 语音合成引擎

- 引擎：Voicebox HTTP API（POST /generate → SSE 轮询 → GET /audio）
- 调用方：``src.agents.streamer.utterance_queue``（编排层确定性调用）
- 自治生命周期：``handle_speech()`` 入口自动 ensure_setup，调用方无需预调
- 错误语义：合成失败主动发 ``tts.utterance.failed`` 并 raise，由队列兜底
- 接受可选 ``utterance_id`` 参数：传入时按 ``tts.utterance.*`` 生命周期发布
  事件；缺省/None 时静默跳过（手动 / 直调场景无 utterance 上下文）
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
from .common import (
    build_stats_dict,
    compute_duration_ms,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
)

if TYPE_CHECKING:
    pass


CHANNELS = 1
DTYPE = np.int16
BLOCKSIZE = 1024


class VoiceboxProvider:
    """Voicebox 语音合成引擎。

    引擎通过 ``handle_speech(text, utterance_id)`` 暴露唯一入口；调用方
    （编排队列）无需关心 setup / cleanup 生命周期——``handle_speech`` 开头
    自动 ensure_setup。
    """

    PROVIDER_NAME = "voicebox"

    class ConfigSchema(BaseConfig):
        type: str = "voicebox"
        host: str = Field(default="127.0.0.1", description="Voicebox API 主机地址")
        port: int = Field(default=17493, ge=1, le=65535, description="Voicebox API 端口")
        profile_id: str = Field(
            default="",
            description="音色 profile ID（在 Voicebox 中克隆音色后获得；留空时合成会报错提示）",
        )
        language: str = Field(default="zh", description="语言代码")
        sample_rate: int = Field(default=24000, ge=8000, le=48000, description="输出采样率")
        generation_timeout_ms: int = Field(default=120000, ge=5000, le=300000, description="生成超时（毫秒）")

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

    # ===== 生命周期 =====

    async def setup(self) -> None:
        """初始化引擎（幂等）。"""
        if self._has_started:
            return

        if self.tts_lock is None:
            self.tts_lock = asyncio.Lock()

        self._session = aiohttp.ClientSession()

        from src.modules.audio import AudioDeviceManager

        manager = AudioDeviceManager(
            sample_rate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
        )
        self.audio_manager = manager

        try:
            async with self._session.get(f"{self.base_url}/health", timeout=aiohttp.ClientTimeout(total=5)):
                self.logger.info("Voicebox 服务连接成功")
        except Exception as e:
            self.logger.warning("Voicebox 健康检查失败: : {}", e)

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
        if self.tts_lock is None:
            self.tts_lock = asyncio.Lock()
        async with self.tts_lock:
            await self._synthesize(text, utterance_id=utterance_id)

    async def _synthesize(self, text: str, utterance_id: Optional[str] = None) -> None:
        """合成并播放（全量引擎：合成完拿完整 PCM，duration_ms 精确计算）

        异常处理：合成（HTTP 三段）或播放（play_audio）任一阶段失败都先发
        failed 事件再向上抛，由编排队列兜底。
        """
        try:
            if not self._session or self._session.closed:
                raise RuntimeError("Voicebox HTTP 会话未初始化")
            if not self.profile_id:
                raise RuntimeError("未配置 profile_id，请先在 Voicebox 中克隆音色并填写配置")

            generation_id = await self._create_generation(text)
            await self._wait_for_completion(generation_id)
            audio_data = await self._download_audio(generation_id)

            # 函数体内导入：单元测试 patch wav_decoder.extract_pcm_from_wav 拦截
            # 解码，顶部导入会使 patch 失效
            from src.modules.tts.wav_decoder import extract_pcm_from_wav

            pcm_data = extract_pcm_from_wav(audio_data)
            audio_array = np.frombuffer(pcm_data, dtype=DTYPE)

            if len(audio_array) == 0:
                self.logger.warning("Voicebox 返回空音频，跳过播放")
                return

            duration_ms = compute_duration_ms(len(audio_array), self.sample_rate)

            await emit_utterance_started(
                self.event_bus,
                utterance_id=utterance_id,
                speech_text=text,
                engine=self.PROVIDER_NAME,
                duration_ms=duration_ms,
            )

            await self.audio_manager.play_audio(audio_array, samplerate=self.sample_rate)
            self.render_count += 1

            await emit_utterance_finished(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                duration_ms=duration_ms,
            )
        except asyncio.CancelledError:
            # 协程取消不会停止已提交给声卡的播放，超时取消时必须显式截断
            if self.audio_manager:
                self.audio_manager.stop_audio()
            self.logger.warning("TTS 渲染被取消（已截断播放）: utterance_id=: {}", utterance_id)
            raise
        except Exception as e:
            self.error_count += 1
            self.logger.opt(exception=True).error("Voicebox 合成/播放失败: : {}", e)
            await emit_utterance_failed(
                self.event_bus,
                utterance_id=utterance_id,
                engine=self.PROVIDER_NAME,
                error_message=f"{type(e).__name__}: {e}",
            )
            raise

    async def _create_generation(self, text: str) -> str:
        """POST /generate"""
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
        """SSE 轮询生成状态"""
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
        """GET /audio 下载音频"""
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

    def get_stats(self) -> Dict[str, Any]:
        return build_stats_dict(
            name=self.__class__.__name__,
            is_connected=self._session is not None and not self._session.closed,
            render_count=self.render_count,
            error_count=self.error_count,
        )


def create_voicebox_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> VoiceboxProvider:
    """工厂：构造 ``VoiceboxProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``。
    """
    return VoiceboxProvider(
        config=config,
        event_bus=event_bus,
    )


__all__ = ["VoiceboxProvider", "create_voicebox_provider"]
