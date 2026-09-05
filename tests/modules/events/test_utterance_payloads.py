"""
TTS utterance 事件 Payload 测试

覆盖：
1. 三个 Payload 类的有效字段构造（含 BasePayload 的 ``id`` 自动生成）。
2. ``UtteranceStartedPayload.duration_ms`` 可为 ``None``（流式引擎合成未完场景）。
3. ``model_dump`` 序列化往返（dump → validate → 等价实例）。
4. 三个事件名通过 ``@register_event`` 装饰器注册到 ``EVENT_REGISTRY``，且
   ``CoreEvents`` 常量字符串与注册键完全一致。

运行: uv run pytest tests/modules/events/test_utterance_payloads.py -v
"""

import pytest
from pydantic import BaseModel

from src.modules.events import (
    EVENT_REGISTRY,
    register_core_events,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)


# =============================================================================
# Fixture：保证 EVENT_REGISTRY 在每次测试前已填充
# =============================================================================


@pytest.fixture(autouse=True)
def _ensure_registry_loaded():
    """调用 ``register_core_events()`` 触发 Payload 模块导入，确保装饰器已执行"""
    register_core_events()
    yield


# =============================================================================
# 构造测试：合法字段
# =============================================================================


class TestStartedPayloadConstruction:
    """UtteranceStartedPayload：合法字段构造（含 duration_ms 显式值）"""

    def test_construct_with_full_fields(self):
        """全量引擎场景：duration_ms=精确合成时长"""
        payload = UtteranceStartedPayload(
            utterance_id="utt_1700000000000_1",
            speech_text="你好世界",
            engine="edge",
            duration_ms=3200,
            timestamp_ms=1700000000000,
        )
        assert payload.utterance_id == "utt_1700000000000_1"
        assert payload.speech_text == "你好世界"
        assert payload.engine == "edge"
        assert payload.duration_ms == 3200
        assert payload.timestamp_ms == 1700000000000

    def test_construct_without_timestamp_uses_default_factory(self):
        """未传 timestamp_ms 时，default_factory (now_ms) 应填入正整数"""
        payload = UtteranceStartedPayload(
            utterance_id="utt_x",
            speech_text="hi",
            engine="gptsovits",
            duration_ms=1500,
        )
        assert payload.timestamp_ms > 0
        assert isinstance(payload.timestamp_ms, int)

    def test_basepayload_id_is_auto_generated(self):
        """BasePayload 的 ``id`` 字段由 default_factory 自动生成 uuid4"""
        payload = UtteranceStartedPayload(
            utterance_id="utt_x",
            speech_text="hi",
            engine="edge",
            duration_ms=100,
        )
        assert payload.id
        # 两次构造得到的 id 应不同
        other = UtteranceStartedPayload(
            utterance_id="utt_x",
            speech_text="hi",
            engine="edge",
            duration_ms=100,
        )
        assert payload.id != other.id


class TestStartedPayloadDurationMsOptional:
    """UtteranceStartedPayload：duration_ms=None 必须被接受（流式引擎合成未完）"""

    def test_duration_ms_none_accepted(self):
        """显式 None：与契约一致，流式引擎合成未完时为 None"""
        payload = UtteranceStartedPayload(
            utterance_id="utt_stream_1",
            speech_text="正在合成…",
            engine="gptsovits",
            duration_ms=None,
            timestamp_ms=1700000000000,
        )
        assert payload.duration_ms is None

    def test_duration_ms_default_is_none(self):
        """未传 duration_ms：默认值就是 None"""
        payload = UtteranceStartedPayload(
            utterance_id="utt_stream_2",
            speech_text="正在合成…",
            engine="omni",
        )
        assert payload.duration_ms is None


class TestFinishedPayloadConstruction:
    """UtteranceFinishedPayload：合法字段构造"""

    def test_construct_with_required_fields(self):
        """必填字段：utterance_id / engine / duration_ms / timestamp_ms"""
        payload = UtteranceFinishedPayload(
            utterance_id="utt_1700000000000_1",
            engine="edge",
            duration_ms=3200,
            timestamp_ms=1700000000000,
        )
        assert payload.utterance_id == "utt_1700000000000_1"
        assert payload.engine == "edge"
        assert payload.duration_ms == 3200
        assert payload.timestamp_ms == 1700000000000

    def test_duration_ms_required_not_optional(self):
        """finished 的 duration_ms 是必填 int（播放完成时必出值）"""
        with pytest.raises(Exception):
            UtteranceFinishedPayload(  # type: ignore[call-arg]
                utterance_id="utt_x",
                engine="edge",
                timestamp_ms=1700000000000,
            )


class TestFailedPayloadConstruction:
    """UtteranceFailedPayload：合法字段构造"""

    def test_construct_with_required_fields(self):
        """必填字段：utterance_id / engine / error_message / timestamp_ms"""
        payload = UtteranceFailedPayload(
            utterance_id="utt_1700000000000_2",
            engine="gptsovits",
            error_message="WebSocket disconnected before first chunk",
            timestamp_ms=1700000000000,
        )
        assert payload.utterance_id == "utt_1700000000000_2"
        assert payload.engine == "gptsovits"
        assert payload.error_message == "WebSocket disconnected before first chunk"
        assert payload.timestamp_ms == 1700000000000

    def test_error_message_required(self):
        """failed 的 error_message 是必填（无默认值兜底）"""
        with pytest.raises(Exception):
            UtteranceFailedPayload(  # type: ignore[call-arg]
                utterance_id="utt_x",
                engine="edge",
                timestamp_ms=1700000000000,
            )


# =============================================================================
# 序列化往返测试
# =============================================================================


class TestSerializationRoundTrip:
    """model_dump → model_validate 应还原等价实例"""

    def _roundtrip(self, original: BaseModel) -> BaseModel:
        dumped = original.model_dump()
        # type: ignore[attr-defined] — model_validate 是泛型类方法，静态推断需要 type[BaseModel] 子类
        return type(original).model_validate(dumped)  # type: ignore[reportAttributeAccessIssue]

    def test_started_payload_roundtrip(self):
        """UtteranceStartedPayload 序列化往返"""
        original = UtteranceStartedPayload(
            utterance_id="utt_1",
            speech_text="你好",
            engine="edge",
            duration_ms=2500,
            timestamp_ms=1700000000000,
        )
        restored = self._roundtrip(original)
        assert isinstance(restored, UtteranceStartedPayload)
        assert restored.utterance_id == original.utterance_id
        assert restored.speech_text == original.speech_text
        assert restored.engine == original.engine
        assert restored.duration_ms == original.duration_ms
        assert restored.timestamp_ms == original.timestamp_ms

    def test_started_payload_roundtrip_with_none_duration(self):
        """duration_ms=None 应在 dump / validate 往返后保持 None"""
        original = UtteranceStartedPayload(
            utterance_id="utt_2",
            speech_text="正在合成",
            engine="omni",
            duration_ms=None,
            timestamp_ms=1700000000000,
        )
        dumped = original.model_dump()
        assert dumped["duration_ms"] is None
        restored = self._roundtrip(original)
        # type: ignore[reportAttributeAccessIssue] — restored 静态类型为 BaseModel，已知子类必带此字段
        assert restored.duration_ms is None  # type: ignore[reportAttributeAccessIssue]

    def test_finished_payload_roundtrip(self):
        """UtteranceFinishedPayload 序列化往返"""
        original = UtteranceFinishedPayload(
            utterance_id="utt_1",
            engine="edge",
            duration_ms=3200,
            timestamp_ms=1700000000000,
        )
        restored = self._roundtrip(original)
        assert isinstance(restored, UtteranceFinishedPayload)
        assert restored.utterance_id == original.utterance_id
        assert restored.engine == original.engine
        assert restored.duration_ms == original.duration_ms
        assert restored.timestamp_ms == original.timestamp_ms

    def test_failed_payload_roundtrip(self):
        """UtteranceFailedPayload 序列化往返"""
        original = UtteranceFailedPayload(
            utterance_id="utt_1",
            engine="gptsovits",
            error_message="synthesis timeout",
            timestamp_ms=1700000000000,
        )
        restored = self._roundtrip(original)
        assert isinstance(restored, UtteranceFailedPayload)
        assert restored.utterance_id == original.utterance_id
        assert restored.engine == original.engine
        assert restored.error_message == original.error_message
        assert restored.timestamp_ms == original.timestamp_ms


# =============================================================================
# 注册表测试：事件名 + 常量 + Payload 类三者绑定
# =============================================================================


class TestEventRegistration:
    """三个事件名应在 ``register_core_events()`` 后注册到 ``EVENT_REGISTRY``"""

    def test_core_events_constants_have_expected_string_values(self):
        """``CoreEvents`` 常量字符串与设计契约一致"""
        assert CoreEvents.TTS_UTTERANCE_STARTED == "tts.utterance.started"
        assert CoreEvents.TTS_UTTERANCE_FINISHED == "tts.utterance.finished"
        assert CoreEvents.TTS_UTTERANCE_FAILED == "tts.utterance.failed"

    def test_three_event_names_are_in_registry(self):
        """EVENT_REGISTRY 中存在三个事件键"""
        assert CoreEvents.TTS_UTTERANCE_STARTED in EVENT_REGISTRY
        assert CoreEvents.TTS_UTTERANCE_FINISHED in EVENT_REGISTRY
        assert CoreEvents.TTS_UTTERANCE_FAILED in EVENT_REGISTRY

    def test_started_event_resolves_to_correct_payload_class(self):
        """``tts.utterance.started`` → ``UtteranceStartedPayload``"""
        assert EVENT_REGISTRY[CoreEvents.TTS_UTTERANCE_STARTED] is UtteranceStartedPayload

    def test_finished_event_resolves_to_correct_payload_class(self):
        """``tts.utterance.finished`` → ``UtteranceFinishedPayload``"""
        assert EVENT_REGISTRY[CoreEvents.TTS_UTTERANCE_FINISHED] is UtteranceFinishedPayload

    def test_failed_event_resolves_to_correct_payload_class(self):
        """``tts.utterance.failed`` → ``UtteranceFailedPayload``"""
        assert EVENT_REGISTRY[CoreEvents.TTS_UTTERANCE_FAILED] is UtteranceFailedPayload

    def test_payloads_carry_reverse_reference_to_their_event_name(self):
        """三个 Payload 类的 ``_registered_event_name`` 与 ``CoreEvents`` 常量字符串一致"""
        assert getattr(UtteranceStartedPayload, "_registered_event_name", None) == CoreEvents.TTS_UTTERANCE_STARTED
        assert getattr(UtteranceFinishedPayload, "_registered_event_name", None) == CoreEvents.TTS_UTTERANCE_FINISHED
        assert getattr(UtteranceFailedPayload, "_registered_event_name", None) == CoreEvents.TTS_UTTERANCE_FAILED


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])