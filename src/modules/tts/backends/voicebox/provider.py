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
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

import aiohttp
import numpy as np

from src.modules.config.core_schemas import VoiceboxParams
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from ...common import build_stats_dict, run_full_engine_speech

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

    # 参数 Schema 单一事实源在 config 侧（core_schemas），此处仅别名引用
    ConfigSchema = VoiceboxParams

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        audio_sink: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        # 音频分接（AudioSink 协议形状；口型分析等消费者经装配链注入）
        self.audio_sink = audio_sink
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

        # 函数内 import：src.modules.audio 顶层即拉起 numpy（重型数值依赖），仅在需要音频设备管理时加载
        from src.modules.audio import AudioDeviceManager

        manager = AudioDeviceManager(
            sample_rate=self.sample_rate,
            channels=CHANNELS,
            dtype=DTYPE,
            sink=self.audio_sink,
        )
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

    # ===== 业务方法 =====

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        """TTS 播放入口（发声四步前奏走公共入口，引擎只剩合成差异）。

        Args:
            text: 要合成的文本。
            utterance_id: 一次发声实例的唯一 ID；非 None 时按
                ``tts.utterance.*`` 生命周期发布事件。
        """
        await run_full_engine_speech(self, text=text, utterance_id=utterance_id, synthesize=self._synthesize_audio)

    async def _synthesize_audio(self, text: str) -> Tuple[np.ndarray, int]:
        """文本 → 音频（Voicebox HTTP 三段：创建任务 → 轮询状态 → 下载 WAV 并解码）"""
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
        return np.frombuffer(pcm_data, dtype=DTYPE), self.sample_rate

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
    audio_sink: Optional[Any] = None,
) -> VoiceboxProvider:
    """工厂：构造 ``VoiceboxProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``。
    """
    return VoiceboxProvider(
        config=config,
        event_bus=event_bus,
        audio_sink=audio_sink,
    )


__all__ = ["VoiceboxProvider", "create_voicebox_provider"]
