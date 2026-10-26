"""四个 TTS 引擎的共同契约：utterance 生命周期事件、失败上抛、TTSProvider 协议、已退役的工具化入口"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.tts import TTSProvider
from src.modules.tts.backends.edge.provider import EdgeTTSProvider
from src.modules.tts.backends.gptsovits.provider import DTYPE, GPTSoVITSProvider
from src.modules.tts.backends.omni.provider import OmniTTSProvider
from src.modules.tts.backends.voicebox.provider import VoiceboxProvider
from src.modules.tts.protocol import TTSProvider as TTSProviderDirect
from src.modules.tts.protocol import TtsObservation
from tests.modules.tts.helpers import (
    FakeLLM,
    audio_response,
    fake_http_session,
    make_audio_manager,
    record_lifecycle,
    setup_with_audio_manager,
)


class _Engine:
    """一个引擎的装配与合成替身。

    build 构造 Provider；prepare 在 setup 之后补齐替身；synth_patches 打开成功/失败的合成补丁。
    """

    name: str
    duration_ms: int | None  # started 事件里的时长：全量引擎是精确值，流式引擎是 None
    sent_text: Callable[[], str]  # synth_patches 之后可读：送进合成的文本（规范化后）

    def build(self, bus: EventBus | None, fail: bool) -> Any:
        raise NotImplementedError

    def setup_patches(self, stack: ExitStack) -> None:
        return None

    async def prepare(self, provider: Any) -> None:
        return None

    def synth_patches(self, provider: Any, stack: ExitStack, fail: bool) -> None:
        raise NotImplementedError


class _Edge(_Engine):
    name, duration_ms = "edge_tts", 1000  # 48000 样本 @ 48kHz

    def build(self, bus, fail):
        return EdgeTTSProvider(config={"type": "edge_tts"}, event_bus=bus)

    def synth_patches(self, provider, stack, fail):
        synth = AsyncMock(side_effect=RuntimeError("boom") if fail else None)
        synth.return_value = (np.zeros(48000, dtype=np.float32), 48000)
        stack.enter_context(patch.object(provider, "_edge_tts_synthesize", new=synth))
        self.sent_text = lambda: synth.call_args.args[0]


class _Voicebox(_Engine):
    name, duration_ms = "voicebox", 2000  # 48000 样本 @ 24kHz

    def build(self, bus, fail):
        return VoiceboxProvider(config={"type": "voicebox", "profile_id": "p1", "sample_rate": 24000}, event_bus=bus)

    def setup_patches(self, stack):
        stack.enter_context(fake_http_session())  # setup 的健康检查不打真实网络

    def synth_patches(self, provider, stack, fail):
        create = AsyncMock(side_effect=RuntimeError("boom")) if fail else AsyncMock(return_value="gen-1")
        stack.enter_context(patch.object(provider, "_create_generation", new=create))
        self.sent_text = lambda: create.call_args.args[0]
        stack.enter_context(patch.object(provider, "_wait_for_completion", new=AsyncMock()))
        stack.enter_context(patch.object(provider, "_download_audio", new=AsyncMock(return_value=b"raw")))
        stack.enter_context(patch("src.modules.tts.wav_decoder.extract_pcm_from_wav", return_value=bytes(48000 * 2)))


class _GptSovits(_Engine):
    name, duration_ms = "gptsovits", None  # 流式引擎：合成未完，started 时长为 None

    def build(self, bus, fail):
        # api_v2 每请求必填参考音频，走合成路径要用已配置参考音频的引擎
        config = {"type": "gptsovits", "ref_audio_path": "referenceAudio/ref.wav", "prompt_text": "参考转写"}
        return GPTSoVITSProvider(config=config, event_bus=bus)

    async def prepare(self, provider):
        provider.tts_client = MagicMock()  # 预热任务走替身客户端，不连真实服务
        if provider._warmup_task is not None:  # setup 调度的预热任务要收尾，避免测试 loop 关闭时残留
            await asyncio.gather(provider._warmup_task, return_exceptions=True)

    def synth_patches(self, provider, stack, fail):
        stream = MagicMock(side_effect=RuntimeError("boom")) if fail else MagicMock(return_value=iter([b"c1", b"c2"]))
        stack.enter_context(patch.object(provider, "tts_client", new=MagicMock(tts_stream=stream)))
        self.sent_text = lambda: stream.call_args.kwargs["text"]

        async def decode(chunk: bytes, dtype: Any = None) -> np.ndarray:
            return np.arange(1024, dtype=DTYPE)

        stack.enter_context(patch("src.modules.tts.wav_decoder.decode_wav_chunk", new=decode))


class _Omni(_Engine):
    name, duration_ms = "omni_tts", 100  # 2400 样本 @ 24kHz

    def build(self, bus, fail):
        self.llm = FakeLLM(error=RuntimeError("boom")) if fail else FakeLLM(audio_response())
        return OmniTTSProvider(config={"type": "omni_tts", "voice": "Chelsie"}, event_bus=bus, llm_service=self.llm)

    def synth_patches(self, provider, stack, fail):
        self.sent_text = lambda: self.llm.calls[0]["input"]  # 合成走 FakeLLM，无需补丁


ENGINES = [_Edge(), _Voicebox(), _GptSovits(), _Omni()]


async def _speak(
    engine: _Engine, bus: EventBus | None, *, utterance_id: str | None, fail: bool = False, text: str = "你好"
) -> None:
    provider = engine.build(bus, fail)
    with ExitStack() as stack:
        engine.setup_patches(stack)
        await setup_with_audio_manager(provider, make_audio_manager())
    await engine.prepare(provider)
    with ExitStack() as stack:
        engine.synth_patches(provider, stack, fail)
        await provider.handle_speech(text, utterance_id=utterance_id)


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.name)
async def test_utterance_events_started_then_finished(engine: _Engine, event_bus: EventBus) -> None:
    captured = record_lifecycle(event_bus)
    await _speak(engine, event_bus, utterance_id="utt_1")
    await event_bus.cleanup()  # 等后台派发完成

    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED, CoreEvents.TTS_UTTERANCE_FINISHED]
    started, finished = (payload for _, payload in captured)
    assert isinstance(started, UtteranceStartedPayload) and isinstance(finished, UtteranceFinishedPayload)
    assert (started.utterance_id, started.speech_text, started.engine) == ("utt_1", "你好", engine.name)
    assert started.duration_ms == engine.duration_ms
    assert (finished.utterance_id, finished.engine) == ("utt_1", engine.name)
    if engine.duration_ms is not None:
        assert finished.duration_ms == engine.duration_ms


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.name)
async def test_event_keeps_the_original_text_while_synthesis_gets_the_normalized_one(
    engine: _Engine, event_bus: EventBus
) -> None:
    """含符号文本：事件 speech_text 传规范化前原文（字幕等展示面不被清洗改写），清洗只进合成侧。"""
    captured = record_lifecycle(event_bus)
    await _speak(engine, event_bus, utterance_id="utt_sym", text="大家好～(鼓掌)")
    await event_bus.cleanup()

    started = next(payload for name, payload in captured if name == CoreEvents.TTS_UTTERANCE_STARTED)
    assert started.speech_text == "大家好～(鼓掌)"
    sent = engine.sent_text()
    assert sent != started.speech_text and "～" not in sent  # 符号映射为停顿，与展示文本分离


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.name)
async def test_no_utterance_id_or_no_bus_emits_nothing_and_does_not_raise(engine: _Engine, event_bus: EventBus) -> None:
    captured = record_lifecycle(event_bus)
    await _speak(engine, event_bus, utterance_id=None)  # 手动/直调场景无 utterance 上下文
    await _speak(engine, None, utterance_id="utt_no_bus")  # event_bus=None 静默跳过
    await event_bus.cleanup()
    assert captured == []


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.name)
async def test_synthesis_failure_emits_failed_and_reraises(engine: _Engine, event_bus: EventBus) -> None:
    captured = record_lifecycle(event_bus)
    with pytest.raises(RuntimeError, match="boom"):  # 异常继续上抛，由编排队列兜底
        await _speak(engine, event_bus, utterance_id="utt_fail", fail=True)
    await event_bus.cleanup()

    failed = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert len(failed) == 1 and isinstance(failed[0], UtteranceFailedPayload)
    assert (failed[0].utterance_id, failed[0].engine, failed[0].speech_text) == ("utt_fail", engine.name, "你好")
    assert "boom" in failed[0].error_message
    assert not any(name == CoreEvents.TTS_UTTERANCE_FINISHED for name, _ in captured)


def _instances() -> list[Any]:
    return [
        EdgeTTSProvider(config={}),
        GPTSoVITSProvider(config={}),
        VoiceboxProvider(config={}),
        OmniTTSProvider(config={}, llm_service=object()),  # llm_service 是 Omni 的构造硬前置
    ]


@pytest.mark.parametrize("provider", _instances(), ids=lambda p: type(p).__name__)
def test_engine_conforms_to_protocol_and_has_no_retired_entrypoints(provider: Any) -> None:
    assert isinstance(provider, TTSProvider)
    assert provider.name == type(provider).PROVIDER_NAME and hasattr(type(provider), "ConfigSchema")
    assert not hasattr(provider, "invoke") and not hasattr(provider, "list_tools")  # 工具化已退役
    assert "output_device_name" not in type(provider).ConfigSchema.model_fields
    assert not hasattr(provider, "output_device_name")


@pytest.mark.parametrize("provider", _instances(), ids=lambda p: type(p).__name__)
def test_engine_conforms_to_observation_protocol(provider: Any) -> None:
    """观测面契约：四个引擎都满足 TtsObservation（dashboard 只读依赖按它收窄）。"""
    assert isinstance(provider, TtsObservation)


def test_protocol_rejects_incomplete_objects_and_has_one_public_identity() -> None:
    class MissingHandleSpeech:  # 补齐其他成员以隔离单点缺失
        PROVIDER_NAME = "fake"
        name = "fake"

        class ConfigSchema:
            pass

        async def setup(self) -> None: ...
        async def cleanup(self) -> None: ...
        def get_stats(self) -> dict:
            return {}

    assert not any(isinstance(obj, TTSProvider) for obj in (MissingHandleSpeech(), object(), {}, None))
    assert TTSProvider is TTSProviderDirect  # 包级 re-export 与协议模块是同一对象


# 各引擎的特有行为


async def test_edge_skips_blank_text_and_lazily_sets_up(event_bus: EventBus) -> None:
    provider = EdgeTTSProvider(config={"type": "edge_tts"}, event_bus=event_bus)
    assert provider._has_started is False
    mgr = await setup_with_audio_manager(provider)
    assert provider._has_started is True

    await provider.handle_speech("")  # 空文本跳过渲染，不增计数
    assert provider.render_count == 0
    synth = AsyncMock(return_value=(np.zeros(48000, dtype=np.float32), 48000))
    with patch.object(provider, "_edge_tts_synthesize", new=synth):
        await provider.handle_speech("你好")
    assert provider.render_count == 1 and mgr.play_audio.await_count == 1


async def test_edge_synthesize_failure_propagates_instead_of_silent_audio(event_bus: EventBus) -> None:
    """真路径失败上抛：直压 edge_tts.Communicate，穿过真实方法体。

    上面参数化的失败契约用整方法替身换掉 ``_edge_tts_synthesize``，守不住
    方法体内部的吞错——引擎曾把合成异常吃掉并返回静音假音频（错误计数与
    failed 事件全被绕过），只有真路径红灯能拦住这类回归。
    """
    provider = EdgeTTSProvider(config={"type": "edge_tts"}, event_bus=event_bus)
    fake_edge = MagicMock(Communicate=MagicMock(side_effect=RuntimeError("boom")))
    with patch("src.modules.tts.backends.edge.provider.edge_tts", fake_edge, create=True):
        with pytest.raises(RuntimeError, match="boom"):
            await provider._edge_tts_synthesize("你好")


async def test_voicebox_without_profile_id_fails_before_synthesis(event_bus: EventBus) -> None:
    provider = VoiceboxProvider(config={"type": "voicebox"}, event_bus=event_bus)  # 不带 profile_id
    with fake_http_session():
        await setup_with_audio_manager(provider)
    with pytest.raises(RuntimeError, match="profile_id"):
        await provider.handle_speech("x")
