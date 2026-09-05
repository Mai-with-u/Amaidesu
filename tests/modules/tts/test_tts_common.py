"""TTS 共享函数模块（common.py）测试

覆盖：
- ``compute_duration_ms`` 从 PCM 样本数 + 采样率计算毫秒
- ``build_stats_dict`` 构造统一状态统计
- ``emit_utterance_started`` / ``finished`` / ``failed`` 通过 EventBus 发布对应
  事件；``event_bus=None`` 时静默不抛

``ok_result`` / ``fail_result`` 在工具化退役后已删除，相应测试随之移除。
"""

from __future__ import annotations

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.tts.common import (
    build_stats_dict,
    compute_duration_ms,
    emit_utterance_failed,
    emit_utterance_finished,
    emit_utterance_started,
)


# =============================================================================
# compute_duration_ms
# =============================================================================


class TestComputeDurationMs:
    """compute_duration_ms 计算时长"""

    def test_one_second_of_audio(self):
        """48000 样本 @ 48000Hz → 1000ms"""
        assert compute_duration_ms(48000, 48000) == 1000

    def test_three_seconds_of_audio(self):
        """96000 样本 @ 32000Hz → 3000ms"""
        assert compute_duration_ms(96000, 32000) == 3000

    def test_fractional_seconds_truncates_to_int(self):
        """不足 1 毫秒的尾数被整数除法截断"""
        # 1000 样本 @ 48000Hz → 20ms（1000*1000//48000 = 20）
        assert compute_duration_ms(1000, 48000) == 20

    def test_zero_samples_returns_zero(self):
        assert compute_duration_ms(0, 48000) == 0

    def test_invalid_sample_rate_returns_zero(self):
        """采样率 ≤ 0 时防御性返回 0"""
        assert compute_duration_ms(48000, 0) == 0
        assert compute_duration_ms(48000, -1) == 0


# =============================================================================
# build_stats_dict
# =============================================================================


class TestBuildStatsDict:
    """build_stats_dict 构造统一状态统计"""

    def test_minimal_required_fields(self):
        """仅必填 4 字段"""
        stats = build_stats_dict(
            name="TestProvider",
            is_connected=True,
            render_count=3,
            error_count=1,
        )
        assert stats == {
            "name": "TestProvider",
            "is_connected": True,
            "render_count": 3,
            "error_count": 1,
        }

    def test_extra_fields_merged(self):
        """extra 字段并入结果 dict（OmniTTS 之前多出的 buffer_size 走 extra）"""
        stats = build_stats_dict(
            name="Omni",
            is_connected=False,
            render_count=0,
            error_count=0,
            extra={"buffer_size": 42},
        )
        assert stats["buffer_size"] == 42
        assert stats["name"] == "Omni"

    def test_is_connected_coerced_to_bool(self):
        """is_connected 强制转 bool（避免 None 渗入）"""
        stats = build_stats_dict(name="X", is_connected=None, render_count=0, error_count=0)  # type: ignore[arg-type]
        assert stats["is_connected"] is False


# =============================================================================
# emit_utterance_* : 通过 EventBus 发事件 / event_bus=None 静默
# =============================================================================


@pytest.fixture
async def event_bus() -> EventBus:
    bus = EventBus()
    try:
        yield bus
    finally:
        await bus.cleanup()


@pytest.mark.asyncio
class TestEmitUtteranceStarted:
    """emit_utterance_started"""

    async def test_emits_to_event_bus(self, event_bus: EventBus):
        """正常传入 event_bus：发 tts.utterance.started 事件"""
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus.on(
            CoreEvents.TTS_UTTERANCE_STARTED,
            capture,
            model_class=UtteranceStartedPayload,
        )

        await emit_utterance_started(
            event_bus,
            utterance_id="utt_1",
            speech_text="你好",
            engine="edge",
            duration_ms=2000,
        )

        # 等待后台任务完成
        await event_bus.cleanup()

        assert len(captured) == 1
        event_name, payload = captured[0]
        assert event_name == CoreEvents.TTS_UTTERANCE_STARTED
        assert isinstance(payload, UtteranceStartedPayload)
        assert payload.utterance_id == "utt_1"
        assert payload.speech_text == "你好"
        assert payload.engine == "edge"
        assert payload.duration_ms == 2000
        assert payload.timestamp_ms > 0

    async def test_duration_ms_none_for_streaming_engines(self, event_bus: EventBus):
        """duration_ms=None：流式引擎合成未完场景"""
        captured: list[UtteranceStartedPayload] = []

        async def capture(event_name: str, data: UtteranceStartedPayload, source: str) -> None:
            captured.append(data)

        event_bus.on(
            CoreEvents.TTS_UTTERANCE_STARTED,
            capture,
            model_class=UtteranceStartedPayload,
        )

        await emit_utterance_started(
            event_bus,
            utterance_id="utt_stream",
            speech_text="正在合成…",
            engine="gptsovits",
            duration_ms=None,
        )
        await event_bus.cleanup()

        assert len(captured) == 1
        assert captured[0].duration_ms is None
        assert captured[0].engine == "gptsovits"

    async def test_none_event_bus_is_silent(self):
        """event_bus=None：静默跳过，不抛"""
        await emit_utterance_started(
            None,  # type: ignore[arg-type]
            utterance_id="utt_x",
            speech_text="hi",
            engine="edge",
            duration_ms=1000,
        )

    async def test_none_utterance_id_is_silent(self, event_bus: EventBus):
        """utterance_id=None：静默跳过（手动/直调场景无 utterance 上下文）"""
        captured: list[UtteranceStartedPayload] = []

        async def capture(event_name: str, data: UtteranceStartedPayload, source: str) -> None:
            captured.append(data)

        event_bus.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)

        await emit_utterance_started(
            event_bus,
            utterance_id=None,
            speech_text="hi",
            engine="edge",
            duration_ms=1000,
        )
        await event_bus.cleanup()

        assert captured == []


@pytest.mark.asyncio
class TestEmitUtteranceFinished:
    """emit_utterance_finished"""

    async def test_emits_to_event_bus(self, event_bus: EventBus):
        captured: list[UtteranceFinishedPayload] = []

        async def capture(event_name: str, data: UtteranceFinishedPayload, source: str) -> None:
            captured.append(data)

        event_bus.on(
            CoreEvents.TTS_UTTERANCE_FINISHED,
            capture,
            model_class=UtteranceFinishedPayload,
        )

        await emit_utterance_finished(
            event_bus,
            utterance_id="utt_1",
            engine="voicebox",
            duration_ms=3500,
        )
        await event_bus.cleanup()

        assert len(captured) == 1
        assert captured[0].utterance_id == "utt_1"
        assert captured[0].engine == "voicebox"
        assert captured[0].duration_ms == 3500

    async def test_none_event_bus_is_silent(self):
        await emit_utterance_finished(
            None,  # type: ignore[arg-type]
            utterance_id="utt_x",
            engine="edge",
            duration_ms=1000,
        )

    async def test_none_utterance_id_is_silent(self, event_bus: EventBus):
        captured: list[UtteranceFinishedPayload] = []

        async def capture(event_name: str, data: UtteranceFinishedPayload, source: str) -> None:
            captured.append(data)

        event_bus.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        await emit_utterance_finished(
            event_bus,
            utterance_id=None,
            engine="edge",
            duration_ms=1000,
        )
        await event_bus.cleanup()

        assert captured == []


@pytest.mark.asyncio
class TestEmitUtteranceFailed:
    """emit_utterance_failed"""

    async def test_emits_to_event_bus(self, event_bus: EventBus):
        captured: list[UtteranceFailedPayload] = []

        async def capture(event_name: str, data: UtteranceFailedPayload, source: str) -> None:
            captured.append(data)

        event_bus.on(
            CoreEvents.TTS_UTTERANCE_FAILED,
            capture,
            model_class=UtteranceFailedPayload,
        )

        await emit_utterance_failed(
            event_bus,
            utterance_id="utt_1",
            engine="gptsovits",
            error_message="WebSocket closed",
        )
        await event_bus.cleanup()

        assert len(captured) == 1
        assert captured[0].utterance_id == "utt_1"
        assert captured[0].engine == "gptsovits"
        assert captured[0].error_message == "WebSocket closed"

    async def test_none_event_bus_is_silent(self):
        await emit_utterance_failed(
            None,  # type: ignore[arg-type]
            utterance_id="utt_x",
            engine="edge",
            error_message="boom",
        )

    async def test_none_utterance_id_is_silent(self, event_bus: EventBus):
        captured: list[UtteranceFailedPayload] = []

        async def capture(event_name: str, data: UtteranceFailedPayload, source: str) -> None:
            captured.append(data)

        event_bus.on(CoreEvents.TTS_UTTERANCE_FAILED, capture, model_class=UtteranceFailedPayload)

        await emit_utterance_failed(
            event_bus,
            utterance_id=None,
            engine="edge",
            error_message="boom",
        )
        await event_bus.cleanup()

        assert captured == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
