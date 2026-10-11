"""OmniTTSProvider - 全模态大模型语音合成引擎（omni_tts）

- 引擎：经 LLMManager 语音 profile 调用带语音输出模态的大模型（OpenAI
  兼容线协议 ``modalities=audio`` 形态，qwen-omni / gpt-4o-audio 等），
  全量收齐音频后经 AudioDeviceManager 播放
- 调用方：``src.agents.streamer.utterance_queue``（编排层确定性调用）
- 自治生命周期：``handle_speech()`` 入口自动 ensure_setup，调用方无需预调
- 错误语义：合成失败主动发 ``tts.utterance.failed`` 并 raise，由队列兜底
- 接受可选 ``utterance_id`` 参数：传入时按 ``tts.utterance.*`` 生命周期发布
  事件；缺省/None 时静默跳过（手动 / 直调场景无 utterance 上下文）

红线边界：引擎是被动驱动的工具，只做"文本→音频"纯转换——说什么永远由
主播 Agent 定稿；``style_prompt`` 仅静态注入语音风格，不承载台词内容。
chat 形态的对面是生成模型，存在改写台词的可能，引擎以 transcript 与输入
文本比对告警暴露该偏差，不静默吞掉。
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Dict, Optional, Protocol

import numpy as np

from src.modules.config.core_schemas import OmniTTSParams
from src.modules.events.event_bus import EventBus
from src.modules.llm.bootstrap import validate_profile_binding
from src.modules.llm.payload import Response
from src.modules.logging import get_logger
from ...common import (
    build_stats_dict,
    compute_duration_ms,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
    normalize_tts_text,
)

DTYPE = np.int16


class AudioLLMInvoker(Protocol):
    """TTS 引擎消费的 LLM 窄接口（结构契约；LLMManager 满足即注入）。

    引擎只依赖音频入口，不接触 profile 调度与计费面——注入窄接口而非
    具体类型，测试以任意满足形状的替身即可。
    """

    async def generate_audio(
        self,
        input: Any,
        *,
        profile: Optional[str] = None,
        voice: str,
        audio_format: str = "wav",
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        interrupt: Optional[Any] = None,
    ) -> Response: ...


class OmniTTSProvider:
    """全模态大模型语音合成引擎。

    引擎通过 ``handle_speech(text, utterance_id)`` 暴露唯一入口；调用方
    （编排队列）无需关心 setup / cleanup 生命周期——``handle_speech`` 开头
    自动 ensure_setup。
    """

    PROVIDER_NAME = "omni_tts"

    # 参数 Schema 单一事实源在 config 侧（core_schemas），此处仅别名引用
    ConfigSchema = OmniTTSParams

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        audio_sink: Optional[Any] = None,
        llm_service: Optional[AudioLLMInvoker] = None,
    ) -> None:
        if llm_service is None:
            raise RuntimeError("omni_tts 引擎需要注入 LLM 服务（llm_service），装配链未提供")
        self.config = config
        self.event_bus = event_bus
        # 音频分接（AudioSink 协议形状；口型分析等消费者经装配链注入）
        self.audio_sink = audio_sink
        # LLM 窄接口引用（仅 generate_audio；不接触 profile 调度与计费面）
        self._llm: AudioLLMInvoker = llm_service
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema.from_dict(config)
        self.llm_profile = self.typed_config.llm_profile
        self.voice = self.typed_config.voice
        self.audio_format = self.typed_config.audio_format
        self.sample_rate = self.typed_config.sample_rate
        self.style_prompt = self.typed_config.style_prompt

        self.render_count = 0
        self.error_count = 0
        self.audio_manager: Any = None
        self._is_connected = False
        self._has_started = False
        self.tts_lock: Optional[asyncio.Lock] = None

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    # ===== 生命周期 =====

    async def setup(self) -> None:
        """初始化引擎（幂等）。

        调用方在装配阶段和 ``handle_speech`` 自治入口都可能触发；重复调用
        早退，不重复初始化。配置缺陷（未知 profile / 未填音色）在此硬错。
        """
        if self._has_started:
            return

        # 封闭集合校验：未知 profile 在首次使用前暴露，不允许静默兜底
        validate_profile_binding(self.llm_profile)
        if not self.voice:
            raise RuntimeError("未配置 voice 音色：请在 [tts.omni_tts].voice 填写端点厂商的音色名")

        if self.tts_lock is None:
            self.tts_lock = asyncio.Lock()

        from src.modules.audio import AudioDeviceManager

        manager = AudioDeviceManager(
            sample_rate=self.sample_rate,
            channels=1,
            dtype=DTYPE,
            sink=self.audio_sink,
        )
        self.audio_manager = manager

        self._has_started = True
        self._is_connected = True
        self.logger.info(f"OmniTTSProvider 设置完成（profile={self.llm_profile}, voice={self.voice}）")

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
        """合成并播放语音。

        Args:
            text: 要合成的文本。
            utterance_id: 一次发声实例的唯一 ID；非 None 时按
                ``tts.utterance.*`` 生命周期发布事件。
        """
        if not self._has_started:
            await self.setup()
        text = normalize_tts_text(text)
        if not text.strip():
            self.logger.debug("文本规范化后为空，跳过渲染")
            return
        if self.tts_lock is None:
            self.tts_lock = asyncio.Lock()
        async with self.tts_lock:
            await self._synthesize(text, utterance_id=utterance_id)

    async def _synthesize(self, text: str, utterance_id: Optional[str] = None) -> None:
        """调用音频模态生成 → 解码 → 播放（全量引擎：合成完拿完整 PCM）

        异常处理：合成或播放任一阶段失败都先发 failed 事件再向上抛，由
        编排队列兜底。
        """
        try:
            response = await self._llm.generate_audio(
                text,
                profile=self.llm_profile,
                voice=self.voice,
                audio_format=self.audio_format,
                system=self.style_prompt or None,
            )
            if not response.success:
                raise RuntimeError(f"音频合成失败: {response.error or '未知错误'}")
            if not response.audio_base64:
                raise RuntimeError("模型响应未携带音频（audio_base64 为空）")

            audio_array = self._decode_to_pcm(response.audio_base64)
            if len(audio_array) == 0:
                self.logger.warning("音频解码结果为空，跳过播放")
                return

            self._warn_if_rewritten(text, response.content)

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
            self.logger.warning(f"TTS 渲染被取消（已截断播放）: utterance_id={utterance_id}")
            raise
        except Exception as e:
            self.error_count += 1
            self.logger.exception(f"omni_tts 合成/播放失败: {e}")
            await emit_utterance_failed(
                self.event_bus,
                utterance_id=utterance_id,
                speech_text=text,
                engine=self.PROVIDER_NAME,
                error_message=f"{type(e).__name__}: {e}",
            )
            raise

    def _decode_to_pcm(self, audio_base64: str) -> np.ndarray:
        """base64 音频（wav 封装）解码为 int16 PCM 数组。

        函数体内导入：单元测试 patch wav_decoder.extract_pcm_from_wav 拦截
        解码，顶部导入会使 patch 失效。
        """
        from src.modules.tts.wav_decoder import extract_pcm_from_wav

        wav_bytes = base64.b64decode(audio_base64)
        pcm_data = extract_pcm_from_wav(wav_bytes)
        return np.frombuffer(pcm_data, dtype=DTYPE)

    def _warn_if_rewritten(self, requested_text: str, spoken_text: Optional[str]) -> None:
        """模型语音 transcript 与定稿台词不一致时告警（改写偏差可见化）。

        chat 形态对面是生成模型，"演绎"改词会导致语音与字幕/时间线不一致；
        此处仅告警不替换——播出的音频已是既成事实，告警供运营感知。
        """
        if not spoken_text:
            return
        if spoken_text.strip() != requested_text.strip():
            self.logger.warning(
                f"omni_tts transcript 与输入台词不一致（模型可能改写）：输入={requested_text!r} 实说={spoken_text!r}"
            )

    def get_stats(self) -> Dict[str, Any]:
        return build_stats_dict(
            name=self.__class__.__name__,
            is_connected=self._is_connected,
            render_count=self.render_count,
            error_count=self.error_count,
            extra={"llm_profile": self.llm_profile, "voice": self.voice},
        )


def create_omni_tts_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    audio_sink: Optional[Any] = None,
    llm_service: Optional[AudioLLMInvoker] = None,
) -> OmniTTSProvider:
    """工厂：构造 ``OmniTTSProvider`` 实例。

    装配阶段由编排队列持有 Provider 实例并直接调用 ``handle_speech``；
    ``llm_service`` 由组合根注入 LLMManager（本引擎的消费面）。
    """
    return OmniTTSProvider(
        config=config,
        event_bus=event_bus,
        audio_sink=audio_sink,
        llm_service=llm_service,
    )


__all__ = ["AudioLLMInvoker", "OmniTTSProvider", "create_omni_tts_provider"]
