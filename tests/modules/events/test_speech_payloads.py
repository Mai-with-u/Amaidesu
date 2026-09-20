"""streamer.speech 事件 Payload 测试

覆盖：
1. ``StreamerSpeechPayload`` 合法字段构造（含 ``BasePayload`` 的 ``id`` 自动生成）。
2. ``emotion`` / ``emotion_intensity`` 必选（生产者保证必有值；缺省即校验失败）。
3. ``emotion_intensity`` 数值边界（0.0–1.0）。
4. ``timestamp_ms`` 未传时 ``default_factory`` (now_ms) 填入正整数。
5. ``model_dump`` 序列化往返。
6. ``streamer.speech`` 通过 ``@register_event`` 装饰器注册到 ``EVENT_REGISTRY``，
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
from src.modules.types.emotion_vocab import Emotion


@pytest.fixture(autouse=True)
def _ensure_registry_loaded():
    """调用 ``register_core_events()`` 触发 Payload 模块导入，确保装饰器已执行"""
    register_core_events()
    yield


class TestStreamerSpeechPayloadConstruction:
    """StreamerSpeechPayload：合法字段构造"""

    def test_construct_with_full_fields(self):
        """全字段（emotion + emotion_intensity）构造"""
        payload = StreamerSpeechPayload(
            utterance_id="utt_1700000000000_1",
            text="谢谢支持！",
            emotion="happy",
            emotion_intensity=0.8,
            timestamp_ms=1700000000000,
        )
        assert payload.utterance_id == "utt_1700000000000_1"
        assert payload.text == "谢谢支持！"
        assert payload.emotion == "happy"
        assert payload.emotion_intensity == 0.8
        assert payload.timestamp_ms == 1700000000000

    def test_emotion_required(self):
        """emotion 必选：不传即校验失败（生产者保证必有值，无 None 缺省）"""
        with pytest.raises(Exception):
            StreamerSpeechPayload(  # type: ignore[call-arg]
                utterance_id="utt_x",
                text="hi",
                emotion_intensity=0.5,
                timestamp_ms=1700000000000,
            )

    def test_emotion_intensity_required(self):
        """emotion_intensity 必选：不传即校验失败"""
        with pytest.raises(Exception):
            StreamerSpeechPayload(  # type: ignore[call-arg]
                utterance_id="utt_x",
                text="hi",
                emotion="happy",
                timestamp_ms=1700000000000,
            )

    @pytest.mark.parametrize("value", [0.0, 1.0, 0.5])
    def test_emotion_intensity_boundary_accepted(self, value):
        """emotion_intensity 边界值 0.0 / 1.0 / 0.5 合法"""
        payload = StreamerSpeechPayload(
            utterance_id="utt_x",
            text="hi",
            emotion="neutral",
            emotion_intensity=value,
        )
        assert payload.emotion_intensity == value

    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_emotion_intensity_out_of_range_rejected(self, value):
        """emotion_intensity 越界（<0 或 >1）被拒绝"""
        with pytest.raises(Exception):
            StreamerSpeechPayload(
                utterance_id="utt_x",
                text="hi",
                emotion="happy",
                emotion_intensity=value,
            )

    def test_emotion_values_align_with_vocab(self):
        """payload 契约示例值覆盖词表：枚举 17 值全部可作为 emotion 合法值"""
        assert len(Emotion) == 17
        for member in Emotion:
            payload = StreamerSpeechPayload(
                utterance_id="utt_x",
                text="hi",
                emotion=member.value,
                emotion_intensity=0.5,
            )
            assert payload.emotion == member.value

    def test_construct_without_timestamp_uses_default_factory(self):
        """未传 timestamp_ms → default_factory (now_ms) 填正整数"""
        payload = StreamerSpeechPayload(
            utterance_id="utt_x",
            text="hi",
            emotion="neutral",
            emotion_intensity=0.5,
        )
        assert payload.timestamp_ms > 0
        assert isinstance(payload.timestamp_ms, int)

    def test_basepayload_id_is_auto_generated(self):
        """``BasePayload.id`` 由 default_factory 自动生成 uuid4"""
        payload = StreamerSpeechPayload(
            utterance_id="utt_x",
            text="hi",
            emotion="neutral",
            emotion_intensity=0.5,
        )
        assert payload.id
        other = StreamerSpeechPayload(
            utterance_id="utt_x",
            text="hi",
            emotion="neutral",
            emotion_intensity=0.5,
        )
        assert payload.id != other.id


class TestStreamerSpeechPayloadSerialization:
    """序列化往返测试"""

    def test_roundtrip_with_emotion(self):
        """含 emotion + emotion_intensity 的 payload 序列化往返"""
        original = StreamerSpeechPayload(
            utterance_id="utt_1",
            text="你好",
            emotion="happy",
            emotion_intensity=0.7,
            timestamp_ms=1700000000000,
        )
        dumped = original.model_dump()
        restored = StreamerSpeechPayload.model_validate(dumped)
        assert isinstance(restored, StreamerSpeechPayload)
        assert restored.utterance_id == original.utterance_id
        assert restored.text == original.text
        assert restored.emotion == original.emotion
        assert restored.emotion_intensity == original.emotion_intensity
        assert restored.timestamp_ms == original.timestamp_ms


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
