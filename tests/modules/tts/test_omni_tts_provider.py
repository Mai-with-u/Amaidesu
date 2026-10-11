"""OmniTTSProvider 测试（全模态大模型语音合成引擎）

覆盖：
- 构造硬前置：llm_service 缺失即失败（装配 fail-soft 的触发面）
- setup 配置校验：未知 profile 硬错、voice 未填硬错
- 合成成功路径：LLM 窄接口被以正确参数调用、wav 解码播放、
  started → finished 事件序、duration_ms 为精确值
- 失败路径：success=False / audio_base64 缺失发 failed 事件并 raise
- 取消路径：声卡播放显式截断
- transcript 与输入台词不一致时告警（模型改写偏差可见化）
- 空文本跳过渲染；无 utterance_id / 无 event_bus 不发事件
"""

from __future__ import annotations

import asyncio
import base64
import struct
from typing import Any, Optional
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
from src.modules.llm.payload import Response
from src.modules.tts.backends.omni.provider import OmniTTSProvider


# =============================================================================
# 测试替身与夹具
# =============================================================================


def _make_wav_base64(sample_count: int = 2400) -> str:
    """构造最小合法 WAV（24kHz 单声道 int16 静音）的 base64 字符串"""
    data = b"\x00\x00" * sample_count
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        24000,
        24000 * 2,
        2,
        16,
        b"data",
        len(data),
    )
    return base64.b64encode(header + data).decode("ascii")


def _audio_response(
    *,
    audio_base64: Optional[str] = None,
    content: Optional[str] = None,
    success: bool = True,
    error: Optional[str] = None,
) -> Response:
    return Response(
        success=success,
        content=content,
        error=error,
        audio_base64=audio_base64 if audio_base64 is not None else _make_wav_base64(),
        audio_format="wav",
    )


class _FakeLLM:
    """AudioLLMInvoker 形状的记录替身"""

    def __init__(self, response: Optional[Response] = None, error: Optional[Exception] = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_audio(self, input: Any, **kwargs: Any) -> Response:
        self.calls.append({"input": input, **kwargs})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
async def event_bus_async() -> EventBus:
    bus = EventBus()
    try:
        yield bus
    finally:
        await bus.cleanup()


def _build_mock_audio_manager() -> MagicMock:
    mgr = MagicMock()
    # play_audio 被 provider await，必须 AsyncMock
    mgr.play_audio = AsyncMock()
    mgr.stop_audio = MagicMock()
    return mgr


def _patch_audio_manager_constructor(mgr: MagicMock | None = None) -> Any:
    """拦截 AudioDeviceManager 构造（handle_speech 自治 ensure_setup 会触发）"""
    return patch(
        "src.modules.audio.AudioDeviceManager",
        return_value=(mgr or _build_mock_audio_manager()),
    )


def _make_provider(
    event_bus: Optional[EventBus],
    llm: Optional[_FakeLLM] = None,
    config: Optional[dict] = None,
) -> tuple[OmniTTSProvider, _FakeLLM]:
    fake = llm or _FakeLLM(_audio_response())
    provider = OmniTTSProvider(
        config={"type": "omni_tts", "voice": "Chelsie", **(config or {})},
        event_bus=event_bus,
        llm_service=fake,
    )
    return provider, fake


async def _prepare_started_provider(provider: OmniTTSProvider, mgr: MagicMock | None = None) -> None:
    """提前完成 setup，让 handle_speech 的自治入口不再覆盖 mock"""
    with _patch_audio_manager_constructor(mgr):
        await provider.setup()


# =============================================================================
# 构造与配置校验
# =============================================================================


class TestConstruction:
    def test_missing_llm_service_raises(self):
        """llm_service 缺失即构造失败（装配 fail-soft 返回 None 的触发面）"""
        with pytest.raises(RuntimeError, match="llm_service"):
            OmniTTSProvider(config={"type": "omni_tts", "voice": "Chelsie"})

    async def test_unknown_profile_rejected_on_setup(self):
        provider, _ = _make_provider(None, config={"llm_profile": "totally_unknown"})
        with pytest.raises(ValueError, match="totally_unknown"):
            await provider.setup()

    async def test_missing_voice_rejected_on_setup(self):
        provider, _ = _make_provider(None, config={"voice": ""})
        with pytest.raises(RuntimeError, match="voice"):
            await provider.setup()


# =============================================================================
# 合成成功路径
# =============================================================================


@pytest.mark.asyncio
class TestSynthesisSuccess:
    async def test_llm_invoked_with_configured_params(self, event_bus: EventBus):
        """LLM 窄接口按配置的 profile/voice/format/style_prompt 调用"""
        provider, fake = _make_provider(
            event_bus,
            config={"llm_profile": "tts", "voice": "Chelsie", "style_prompt": "语气轻快"},
        )
        mgr = _build_mock_audio_manager()
        await _prepare_started_provider(provider, mgr=mgr)

        await provider.handle_speech("你好，欢迎来到直播间")

        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["input"] == "你好，欢迎来到直播间"
        assert call["profile"] == "tts"
        assert call["voice"] == "Chelsie"
        assert call["audio_format"] == "wav"
        assert call["system"] == "语气轻快"

    async def test_style_prompt_empty_passes_none_system(self, event_bus: EventBus):
        provider, fake = _make_provider(event_bus)
        await _prepare_started_provider(provider)

        await provider.handle_speech("x")

        assert fake.calls[0]["system"] is None

    async def test_playback_with_decoded_pcm(self, event_bus: EventBus):
        """wav 解码后的 PCM 以配置采样率播放"""
        provider, _ = _make_provider(event_bus)
        mgr = _build_mock_audio_manager()
        await _prepare_started_provider(provider, mgr=mgr)

        await provider.handle_speech("你好", utterance_id="utt_1")

        mgr.play_audio.assert_called_once()
        args, kwargs = mgr.play_audio.call_args
        pcm = args[0] if args else kwargs["audio_array"]
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.int16
        assert len(pcm) == 2400
        assert kwargs.get("samplerate") == 24000

    async def test_stats_reflect_render(self, event_bus: EventBus):
        provider, _ = _make_provider(event_bus)
        await _prepare_started_provider(provider)

        await provider.handle_speech("你好")

        stats = provider.get_stats()
        assert stats["render_count"] == 1
        assert stats["error_count"] == 0
        assert stats["is_connected"] is True
        assert stats["llm_profile"] == "tts"
        assert stats["voice"] == "Chelsie"


# =============================================================================
# utterance 生命周期事件（全量引擎：duration_ms 精确）
# =============================================================================


@pytest.mark.asyncio
class TestUtteranceEvents:
    async def test_started_then_finished_with_duration(self, event_bus_async: EventBus):
        provider, _ = _make_provider(event_bus_async)
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        await _prepare_started_provider(provider)
        await provider.handle_speech("你好", utterance_id="utt_omni_1")
        await event_bus_async.cleanup()

        assert [e[0] for e in captured] == [
            CoreEvents.TTS_UTTERANCE_STARTED,
            CoreEvents.TTS_UTTERANCE_FINISHED,
        ]
        started = captured[0][1]
        assert isinstance(started, UtteranceStartedPayload)
        assert started.utterance_id == "utt_omni_1"
        assert started.speech_text == "你好"
        assert started.engine == "omni_tts"
        # 全量引擎：合成完拿完整 PCM → duration_ms 精确
        assert started.duration_ms == 100  # 2400 样本 @ 24kHz

        finished = captured[1][1]
        assert isinstance(finished, UtteranceFinishedPayload)
        assert finished.utterance_id == "utt_omni_1"
        assert finished.engine == "omni_tts"

    async def test_no_utterance_id_emits_no_events(self, event_bus_async: EventBus):
        provider, _ = _make_provider(event_bus_async)
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)

        await _prepare_started_provider(provider)
        await provider.handle_speech("你好")
        await event_bus_async.cleanup()

        assert captured == []

    async def test_none_event_bus_succeeds(self):
        """event_bus=None：手动/直调场景静默跳过事件"""
        provider, _ = _make_provider(None)
        await _prepare_started_provider(provider)
        await provider.handle_speech("hi", utterance_id="utt_no_bus")


# =============================================================================
# 失败与取消路径
# =============================================================================


@pytest.mark.asyncio
class TestFailurePaths:
    async def test_llm_failure_emits_failed_and_raises(self, event_bus_async: EventBus):
        provider, _ = _make_provider(
            event_bus_async, llm=_FakeLLM(_audio_response(success=False, error="boom"))
        )
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FAILED, capture, model_class=UtteranceFailedPayload)

        mgr = _build_mock_audio_manager()
        await _prepare_started_provider(provider, mgr=mgr)

        with pytest.raises(RuntimeError, match="boom"):
            await provider.handle_speech("你好", utterance_id="utt_fail")
        await event_bus_async.cleanup()

        # 合成阶段失败未触达声卡：不截断，只发 failed 事件
        mgr.stop_audio.assert_not_called()
        failed = [e for e in captured if e[0] == CoreEvents.TTS_UTTERANCE_FAILED]
        assert len(failed) == 1
        payload = failed[0][1]
        assert isinstance(payload, UtteranceFailedPayload)
        assert payload.utterance_id == "utt_fail"
        assert payload.engine == "omni_tts"
        assert "boom" in payload.error_message

    async def test_llm_exception_emits_failed_and_raises(self, event_bus_async: EventBus):
        provider, _ = _make_provider(event_bus_async, llm=_FakeLLM(error=RuntimeError("network down")))
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FAILED, capture, model_class=UtteranceFailedPayload)

        await _prepare_started_provider(provider)

        with pytest.raises(RuntimeError, match="network down"):
            await provider.handle_speech("你好", utterance_id="utt_err")
        await event_bus_async.cleanup()

        failed = [e for e in captured if e[0] == CoreEvents.TTS_UTTERANCE_FAILED]
        assert len(failed) == 1

    async def test_empty_audio_payload_fails(self, event_bus_async: EventBus):
        provider, _ = _make_provider(
            event_bus_async, llm=_FakeLLM(_audio_response(audio_base64=""))
        )
        await _prepare_started_provider(provider)

        with pytest.raises(RuntimeError, match="未携带音频"):
            await provider.handle_speech("你好", utterance_id="utt_empty")

    async def test_cancellation_truncates_playback(self, event_bus_async: EventBus):
        """取消不停止已提交声卡的播放——必须显式 stop_audio 截断"""
        provider, _ = _make_provider(event_bus_async)
        mgr = _build_mock_audio_manager()
        mgr.play_audio = AsyncMock(side_effect=asyncio.CancelledError())
        await _prepare_started_provider(provider, mgr=mgr)

        with pytest.raises(asyncio.CancelledError):
            await provider.handle_speech("你好", utterance_id="utt_cancel")

        mgr.stop_audio.assert_called()


# =============================================================================
# 模型改写偏差可见化与渲染跳过
# =============================================================================


@pytest.mark.asyncio
class TestTranscriptGuardAndSkips:
    async def test_rewritten_transcript_warns_but_plays(self, event_bus: EventBus, loguru_capture):
        """transcript 与输入不一致：告警暴露，不阻断播放"""
        provider, _ = _make_provider(
            event_bus, llm=_FakeLLM(_audio_response(content="你好，欢迎来到直播间！"))
        )
        mgr = _build_mock_audio_manager()
        await _prepare_started_provider(provider, mgr=mgr)

        await provider.handle_speech("你好，欢迎来到直播间")

        assert any("transcript 与输入台词不一致" in r["message"] for r in loguru_capture.records)
        mgr.play_audio.assert_called_once()

    async def test_matching_transcript_no_warning(self, event_bus: EventBus, loguru_capture):
        provider, _ = _make_provider(event_bus, llm=_FakeLLM(_audio_response(content="你好，欢迎来到直播间")))
        await _prepare_started_provider(provider)

        await provider.handle_speech("你好，欢迎来到直播间")

        assert not any("transcript" in r["message"] for r in loguru_capture.records)

    async def test_blank_text_skips_render(self, event_bus: EventBus):
        """纯空白文本（规范化后仍为空）不触达 LLM"""
        provider, fake = _make_provider(event_bus)
        await _prepare_started_provider(provider)

        await provider.handle_speech("   ")

        assert fake.calls == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
