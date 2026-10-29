"""虚拟形象适配器的被动事件订阅（尚未移植到面捕合成器的平台）：streamer.speech 反射为表情（后续句等开播再切）、tts.utterance.* 驱动说话状态、订阅生命周期"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider
from src.modules.avatar.platform.warudo.warudo_provider import WarudoProvider
from src.modules.avatar.speech_binding import AvatarBindings, bind_speaking_state, bind_speech_emotion
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)


def _speech(emotion: str = "happy", intensity: float = 0.8) -> StreamerSpeechPayload:
    return StreamerSpeechPayload(utterance_id="utt_1", text="你好呀", emotion=emotion, emotion_intensity=intensity)


async def _emit(bus: EventBus, event_name: str, payload) -> None:
    await bus.emit(event_name, payload, source="test")
    await bus.wait_until_drained()  # 让 handler 跑完


def _weights(warudo: WarudoProvider) -> dict[str, float]:
    return {k: v for k, v in warudo.state_manager.expression_state.weights.items() if v}


async def test_speech_is_reflected_to_warudo_expression_and_tolerated_by_vrchat() -> None:
    bus = EventBus()
    warudo = WarudoProvider(config={})
    assert bind_speech_emotion(bus, warudo, warudo.logger) is not None
    vrchat = VRChatProvider(config={})
    bind_speech_emotion(bus, vrchat, vrchat.logger)  # VRChat 无情绪通道：诚实返回未应用，不抛

    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech("happy", 0.8))
    assert _weights(warudo) == {"happy": pytest.approx(0.8)}  # 默认档案：happy 槽满幅 × 强度


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
    provider = WarudoProvider(config={})
    assert bind_speech_emotion(None, provider, provider.logger) is None  # 工具面-only 形态
    assert bind_speaking_state(None, provider.logger, on_change=lambda s: None) is None


async def test_utterance_events_flip_warudo_talking_head() -> None:
    warudo = WarudoProvider(config={})
    assert warudo.talking_head_task is not None
    bus = EventBus()
    assert bind_speaking_state(bus, warudo.logger, on_change=warudo._set_speaking) is not None

    started = UtteranceStartedPayload(utterance_id="utt_s", speech_text="hi", engine="edge", duration_ms=100)
    finished = UtteranceFinishedPayload(utterance_id="utt_s", engine="edge", duration_ms=100)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, started)
    assert warudo.talking_head_task.is_talking is True
    await _emit(bus, CoreEvents.TTS_UTTERANCE_FINISHED, finished)
    assert warudo.talking_head_task.is_talking is False


async def test_cleanup_unbinds_subscriptions() -> None:
    warudo = WarudoProvider(config={})
    bus = EventBus()
    bindings = AvatarBindings(bus, warudo.logger)
    bindings.attach_emotion_reflection(warudo)
    bindings.attach_speaking_state(warudo._set_speaking)

    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech())
    assert _weights(warudo) == {"happy": pytest.approx(0.8)}

    bindings.close()  # 全量退订（cleanup 内部同款出口）
    await _emit(bus, CoreEvents.STREAMER_SPEECH, _speech("sad", 0.5))
    assert _weights(warudo) == {"happy": pytest.approx(0.8)}  # 退订后不再反射


async def test_follow_up_sentence_switches_expression_when_it_starts_playing() -> None:
    """标了"等播放再切"的句子：发出时不切，轮到它开始播放才切；播放失败的不再切。"""
    provider = MagicMock(set_expression=AsyncMock(return_value=MagicMock(success=True)))
    bus = EventBus()
    bind_speech_emotion(bus, provider, MagicMock())

    def _line(utterance_id: str, emotion: str, *, deferred: bool) -> StreamerSpeechPayload:
        return StreamerSpeechPayload(
            utterance_id=utterance_id, text="台词", emotion=emotion, emotion_intensity=0.7, emotion_at_playback=deferred
        )

    def _started(utterance_id: str) -> UtteranceStartedPayload:
        return UtteranceStartedPayload(utterance_id=utterance_id, speech_text="台词", engine="edge", duration_ms=100)

    def _expressions() -> list[str]:
        return [call.args[0] for call in provider.set_expression.await_args_list]

    await _emit(bus, CoreEvents.STREAMER_SPEECH, _line("utt_1", "scared", deferred=False))
    await _emit(bus, CoreEvents.STREAMER_SPEECH, _line("utt_2", "speechless", deferred=True))
    await _emit(bus, CoreEvents.STREAMER_SPEECH, _line("utt_3", "happy", deferred=True))
    assert _expressions() == ["scared"]

    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, _started("utt_1"))  # 已经切过的句子开播不重复切
    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, _started("utt_2"))
    assert _expressions() == ["scared", "speechless"]

    failed = UtteranceFailedPayload(utterance_id="utt_3", speech_text="台词", engine="edge", error_message="超时")
    await _emit(bus, CoreEvents.TTS_UTTERANCE_FAILED, failed)
    await _emit(bus, CoreEvents.TTS_UTTERANCE_STARTED, _started("utt_3"))
    assert _expressions() == ["scared", "speechless"]
