"""虚拟形象适配器的被动事件订阅：streamer.speech 反射为表情、tts.utterance.* 驱动说话状态、provider 自持订阅生命周期"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider
from src.modules.avatar.platform.vts.vts_provider import VTSProvider
from src.modules.avatar.platform.warudo.warudo_provider import WarudoProvider
from src.modules.avatar.speech_binding import bind_speaking_state, bind_speech_emotion
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import UtteranceFinishedPayload, UtteranceStartedPayload


def _speech(emotion: str = "happy", intensity: float = 0.8) -> StreamerSpeechPayload:
    return StreamerSpeechPayload(utterance_id="utt_1", text="你好呀", emotion=emotion, emotion_intensity=intensity)


async def _emit(bus: EventBus, event_name: str, payload) -> None:
    await bus.emit(event_name, payload, source="test")
    await asyncio.sleep(0.05)  # 让 handler 跑完


def _vts_with_expression() -> VTSProvider:
    provider = VTSProvider(config={})
    provider.expression = MagicMock()
    provider.expression.set_multi_parameter = AsyncMock(return_value=True)
    return provider


async def test_speech_is_reflected_to_vts_parameters_and_tolerated_by_vrchat() -> None:
    bus = EventBus()
    vts = _vts_with_expression()
    assert bind_speech_emotion(bus, vts, vts.logger) is not None
    vrchat = VRChatProvider(config={})
    bind_speech_emotion(bus, vrchat, vrchat.logger)  # VRChat 无情绪通道：诚实返回未应用，不抛

    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech("happy", 0.8))
    # 缩放统一走 scaled_emotion_weights：4 位小数收敛，不带浮点尾差进平台参数
    assert vts.expression.set_multi_parameter.await_args.args[0] == {"MouthSmile": 0.64, "Brows": 0.48}


async def test_provider_error_in_reflection_does_not_break_the_bus() -> None:
    provider = MagicMock(set_expression=AsyncMock(side_effect=RuntimeError("boom")))
    bus = EventBus()
    bind_speech_emotion(bus, provider, MagicMock())
    seen: list[str] = []

    async def follower(event_name, payload, source=None) -> None:
        seen.append(payload.utterance_id)

    bus.on(CoreEvents.STREAMER_SPEECH, follower, model_class=StreamerSpeechPayload)
    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech())
    assert seen == ["utt_1"]  # 后续订阅者仍收到事件


def test_binding_without_event_bus_is_skipped() -> None:
    provider = VTSProvider(config={})
    assert bind_speech_emotion(None, provider, provider.logger) is None  # 工具面-only 形态
    assert bind_speaking_state(None, provider.logger, on_change=lambda s: None) is None


async def test_utterance_events_flip_vts_speaking_flag_and_warudo_talking_head() -> None:
    vts, warudo = VTSProvider(config={}), WarudoProvider(config={})
    assert warudo.talking_head_task is not None
    bus = EventBus()
    for provider in (vts, warudo):
        assert bind_speaking_state(bus, provider.logger, on_change=provider._set_speaking) is not None

    started = UtteranceStartedPayload(utterance_id="utt_s", speech_text="hi", engine="edge", duration_ms=100)
    finished = UtteranceFinishedPayload(utterance_id="utt_s", engine="edge", duration_ms=100)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    assert vts._is_speaking is True and warudo.talking_head_task.is_talking is True
    await _emit(bus, CoreEvents.TTS_UTTERANCE_FINISHED, finished)
    assert vts._is_speaking is False and warudo.talking_head_task.is_talking is False


async def test_cleanup_unbinds_subscriptions() -> None:
    provider = _vts_with_expression()
    provider._has_started = True  # 阻断真实 pyvts 连接：手工置启动态，直接走订阅绑定段
    bus = EventBus()
    provider.event_bus = bus
    provider._speech_emotion_handler = bind_speech_emotion(bus, provider, provider.logger)
    provider._speaking_state_handles = bind_speaking_state(bus, provider.logger, on_change=provider._set_speaking)

    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech())
    assert provider.expression.set_multi_parameter.await_count == 1

    await provider.cleanup()
    assert provider._speech_emotion_handler is None and provider._speaking_state_handles is None
    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech("sad", 0.5))
    assert provider.expression.set_multi_parameter.await_count == 1  # 退订后不再反射
