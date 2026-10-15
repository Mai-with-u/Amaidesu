"""语音/TTS 事件载荷：必选字段、情绪强度边界、序列化往返、事件名与载荷类的注册绑定"""

import pytest
from pydantic import ValidationError

from src.modules.events import EVENT_REGISTRY, register_core_events
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.types.emotion_vocab import Emotion


@pytest.fixture(autouse=True)
def _ensure_registry_loaded():
    register_core_events()


def _speech(**overrides) -> StreamerSpeechPayload:
    fields = {"utterance_id": "utt_x", "text": "hi", "emotion": "neutral", "emotion_intensity": 0.5, **overrides}
    return StreamerSpeechPayload(**fields)


def test_speech_payload_defaults_and_required_fields() -> None:
    payload = _speech(timestamp_ms=1700000000000)
    assert (payload.emotion, payload.emotion_intensity, payload.timestamp_ms) == ("neutral", 0.5, 1700000000000)
    assert payload.id and payload.id != _speech().id  # BasePayload.id 自动生成且每次不同
    assert isinstance(_speech().timestamp_ms, int) and _speech().timestamp_ms > 0  # 缺省由 now_ms 填充

    for missing in ("emotion", "emotion_intensity"):  # 生产者保证必有值，缺省即校验失败
        fields = {"utterance_id": "utt_x", "text": "hi", "emotion": "happy", "emotion_intensity": 0.5}
        del fields[missing]
        with pytest.raises(ValidationError):
            StreamerSpeechPayload(**fields)


@pytest.mark.parametrize(("value", "valid"), [(0.0, True), (0.5, True), (1.0, True), (-0.1, False), (1.1, False)])
def test_speech_emotion_intensity_bounds(value: float, valid: bool) -> None:
    if valid:
        assert _speech(emotion_intensity=value).emotion_intensity == value
    else:
        with pytest.raises(ValidationError):
            _speech(emotion_intensity=value)


def test_every_emotion_vocab_value_is_accepted() -> None:
    assert len(Emotion) == 17
    assert all(_speech(emotion=member.value).emotion == member.value for member in Emotion)


def test_utterance_payload_required_fields_and_streaming_duration() -> None:
    # 流式引擎合成未完时 duration_ms 缺省为 None，且往返后保持 None
    started = UtteranceStartedPayload(utterance_id="u", speech_text="正在合成", engine="omni")
    assert started.duration_ms is None and started.model_dump()["duration_ms"] is None
    assert started.timestamp_ms > 0

    # finished / failed 的关键字段必填：播放完成必出时长，失败也必带原文和原因
    with pytest.raises(ValidationError):
        UtteranceFinishedPayload(utterance_id="u", engine="edge")  # type: ignore[call-arg]
    for missing in ("speech_text", "error_message"):
        fields = {"utterance_id": "u", "speech_text": "hi", "engine": "edge", "error_message": "boom"}
        del fields[missing]
        with pytest.raises(ValidationError):
            UtteranceFailedPayload(**fields)


@pytest.mark.parametrize(
    "payload",
    [
        _speech(emotion="happy", emotion_intensity=0.7, timestamp_ms=1700000000000),
        UtteranceStartedPayload(
            utterance_id="u", speech_text="你好", engine="edge", duration_ms=2500, timestamp_ms=1700000000000
        ),
        UtteranceFinishedPayload(utterance_id="u", engine="edge", duration_ms=3200, timestamp_ms=1700000000000),
        UtteranceFailedPayload(
            utterance_id="u", speech_text="那句", engine="gptsovits", error_message="timeout", timestamp_ms=1700000000000
        ),
    ],
)
def test_payload_roundtrip(payload) -> None:
    assert type(payload).model_validate(payload.model_dump()) == payload


@pytest.mark.parametrize(
    ("constant", "name", "payload_cls"),
    [
        (CoreEvents.STREAMER_SPEECH, "streamer.speech", StreamerSpeechPayload),
        (CoreEvents.TTS_UTTERANCE_STARTED, "tts.utterance.started", UtteranceStartedPayload),
        (CoreEvents.TTS_UTTERANCE_FINISHED, "tts.utterance.finished", UtteranceFinishedPayload),
        (CoreEvents.TTS_UTTERANCE_FAILED, "tts.utterance.failed", UtteranceFailedPayload),
    ],
)
def test_event_name_registered_and_bound_to_payload_class(constant: str, name: str, payload_cls: type) -> None:
    assert constant == name
    assert EVENT_REGISTRY[name] is payload_cls and payload_cls._registered_event_name == name
