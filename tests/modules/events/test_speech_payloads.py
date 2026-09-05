"""streamer.speech 事件 Payload 测试

覆盖：
1. ``StreamerSpeechPayload`` 合法字段构造（含 ``BasePayload`` 的 ``id`` 自动生成）。
2. ``emotion`` 可选，缺省值为 ``None``。
3. ``timestamp_ms`` 未传时 ``default_factory`` (now_ms) 填入正整数。
4. ``model_dump`` 序列化往返。
5. ``streamer.speech`` 通过 ``@register_event`` 装饰器注册到 ``EVENT_REGISTRY``，
   且 ``CoreEvents`` 常量字符串与注册键完全一致。

运行: uv run pytest tests/modules/events/test_speech_payloads.py -v
"""

import pytest

from src.modules.events import (
    EVENT_REGISTRY,
    register_core_events,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.speech import StreamerSpeechPayload


@pytest.fixture(autouse=True)
def _ensure_registry_loaded():
    """调用 ``register_core_events()`` 触发 Payload 模块导入，确保装饰器已执行"""
    register_core_events()
    yield


class TestStreamerSpeechPayloadConstruction:
    """StreamerSpeechPayload：合法字段构造"""

    def test_construct_with_full_fields(self):
        """全字段（含 emotion）构造"""
        payload = StreamerSpeechPayload(
            utterance_id="utt_1700000000000_1",
            text="谢谢支持！",
            emotion="happy",
            timestamp_ms=1700000000000,
        )
        assert payload.utterance_id == "utt_1700000000000_1"
        assert payload.text == "谢谢支持！"
        assert payload.emotion == "happy"
        assert payload.timestamp_ms == 1700000000000

    def test_construct_without_emotion_defaults_to_none(self):
        """不传 emotion → 默认 None"""
        payload = StreamerSpeechPayload(
            utterance_id="utt_x",
            text="hi",
            timestamp_ms=1700000000000,
        )
        assert payload.emotion is None

    def test_construct_without_timestamp_uses_default_factory(self):
        """未传 timestamp_ms → default_factory (now_ms) 填正整数"""
        payload = StreamerSpeechPayload(
            utterance_id="utt_x",
            text="hi",
        )
        assert payload.timestamp_ms > 0
        assert isinstance(payload.timestamp_ms, int)

    def test_basepayload_id_is_auto_generated(self):
        """``BasePayload.id`` 由 default_factory 自动生成 uuid4"""
        payload = StreamerSpeechPayload(
            utterance_id="utt_x",
            text="hi",
        )
        assert payload.id
        other = StreamerSpeechPayload(
            utterance_id="utt_x",
            text="hi",
        )
        assert payload.id != other.id


class TestStreamerSpeechPayloadSerialization:
    """序列化往返测试"""

    def test_roundtrip_with_emotion(self):
        """含 emotion 的 payload 序列化往返"""
        original = StreamerSpeechPayload(
            utterance_id="utt_1",
            text="你好",
            emotion="happy",
            timestamp_ms=1700000000000,
        )
        dumped = original.model_dump()
        restored = StreamerSpeechPayload.model_validate(dumped)
        assert isinstance(restored, StreamerSpeechPayload)
        assert restored.utterance_id == original.utterance_id
        assert restored.text == original.text
        assert restored.emotion == original.emotion
        assert restored.timestamp_ms == original.timestamp_ms

    def test_roundtrip_without_emotion_preserves_none(self):
        """emotion=None 在 dump / validate 往返后保持 None"""
        original = StreamerSpeechPayload(
            utterance_id="utt_2",
            text="正在发言",
            timestamp_ms=1700000000000,
        )
        dumped = original.model_dump()
        assert dumped["emotion"] is None
        restored = StreamerSpeechPayload.model_validate(dumped)
        assert restored.emotion is None


class TestStreamerSpeechEventRegistration:
    """事件注册表绑定"""

    def test_core_events_constant_value(self):
        """``CoreEvents.STREAMER_SPEECH`` 常量字符串与设计契约一致"""
        assert CoreEvents.STREAMER_SPEECH == "streamer.speech"

    def test_event_name_in_registry(self):
        """``streamer.speech`` 在 ``EVENT_REGISTRY`` 中"""
        assert CoreEvents.STREAMER_SPEECH in EVENT_REGISTRY

    def test_event_resolves_to_correct_payload_class(self):
        """``streamer.speech`` → ``StreamerSpeechPayload``"""
        assert EVENT_REGISTRY[CoreEvents.STREAMER_SPEECH] is StreamerSpeechPayload

    def test_payload_carries_reverse_reference(self):
        """`` ``_registered_event_name`` 与 ``CoreEvents`` 常量字符串一致"""
        assert getattr(StreamerSpeechPayload, "_registered_event_name", None) == CoreEvents.STREAMER_SPEECH


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
