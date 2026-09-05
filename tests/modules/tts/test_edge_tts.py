"""EdgeTTSProvider 测试

覆盖：
- ``output_device_name`` 字段已从 ConfigSchema 中删除
- 无 utterance_id 时不发布事件
- 有 utterance_id 时按 started → finished 顺序发布事件；全量引擎
  duration_ms 精确
- 合成失败时主动发 failed 事件并 raise（不再返回 ToolExecutionResult）
- ``handle_speech`` 自治 ensure_setup
"""

from __future__ import annotations

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
from src.modules.tts.edge_tts_tool import EdgeTTSProvider


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def event_bus() -> EventBus:
    bus = EventBus()
    return bus


@pytest.fixture
async def event_bus_async() -> EventBus:
    bus = EventBus()
    try:
        yield bus
    finally:
        await bus.cleanup()


@pytest.fixture
def provider(event_bus: EventBus) -> EdgeTTSProvider:
    return EdgeTTSProvider(
        config={"type": "edge_tts", "voice": "zh-CN-XiaoxiaoNeural"},
        event_bus=event_bus,
    )


async def _prepare_started_provider(
    provider: EdgeTTSProvider,
    *,
    audio_manager: MagicMock | None = None,
) -> MagicMock:
    """提前完成 setup：让 ``handle_speech`` 的自治 ensure_setup 不再覆盖 mock。

    EdgeTTS 的 ``_setup_audio_device`` 走 ``from src.modules.audio import
    AudioDeviceManager``，所以通过 ``src.modules.audio.AudioDeviceManager``
    拦截构造函数本身。返回构造时注入的 mock 便于后续 ``patch.object`` 覆盖。
    """
    if audio_manager is None:
        audio_manager = MagicMock(play_audio=AsyncMock())
    with patch(
        "src.modules.audio.AudioDeviceManager",
        return_value=audio_manager,
    ):
        await provider.setup()
    return audio_manager


# =============================================================================
# 配置 Schema：output_device_name 已删除
# =============================================================================


class TestConfigSchemaCleanup:
    """output_device_name 字段已从 ConfigSchema 中删除"""

    def test_config_schema_does_not_contain_output_device_name(self):
        """ConfigSchema 字段集合中不含 output_device_name"""
        fields = EdgeTTSProvider.ConfigSchema.model_fields
        assert "output_device_name" not in fields

    def test_provider_initializes_without_output_device_name_attr(self, provider: EdgeTTSProvider):
        """Provider 实例不应再有 output_device_name 属性"""
        assert not hasattr(provider, "output_device_name")


# =============================================================================
# 工具化退役：引擎不再实现 ToolProvider 协议
# =============================================================================


class TestDeTooled:
    """工具化退役：list_tools / invoke / ToolSpec 不应再出现"""

    def test_provider_has_no_invoke(self, provider: EdgeTTSProvider):
        """invoke() 入口已删除（ToolProvider 协议退役）"""
        assert not hasattr(provider, "invoke")

    def test_provider_has_no_list_tools(self, provider: EdgeTTSProvider):
        """list_tools() 入口已删除"""
        assert not hasattr(provider, "list_tools")


# =============================================================================
# handle_speech：自治 ensure_setup + 事件发布
# =============================================================================


@pytest.mark.asyncio
class TestHandleSpeechBasic:
    """handle_speech 入口：自治 ensure_setup / 文本空 / 事件"""

    async def test_handle_speech_triggers_lazy_setup(self, event_bus: EventBus):
        """handle_speech 首次调用前未 setup：自动 ensure_setup"""
        provider = EdgeTTSProvider(config={"type": "edge_tts"}, event_bus=event_bus)
        assert provider._has_started is False

        mgr = await _prepare_started_provider(
            provider, audio_manager=MagicMock(play_audio=AsyncMock())
        )

        with (
            patch.object(
                provider,
                "_edge_tts_synthesize",
                new=AsyncMock(return_value=(np.zeros(48000, dtype=np.float32), 48000)),
            ),
            patch.object(provider, "audio_manager", new=mgr),
        ):
            await provider.handle_speech("你好")

        assert provider._has_started is True
        assert provider.render_count == 1

    async def test_handle_speech_empty_text_skips(self, event_bus: EventBus):
        """text 为空：跳过渲染，不发事件、不增计数（setup 仍触发首次）"""
        provider = EdgeTTSProvider(config={"type": "edge_tts"}, event_bus=event_bus)
        await _prepare_started_provider(provider)
        await provider.handle_speech("")
        assert provider.render_count == 0
        assert provider._has_started is True


@pytest.mark.asyncio
class TestUtteranceEventsFullBuffer:
    """全量引擎：started 含精确 duration_ms，finished 与 started duration 一致"""

    async def test_no_utterance_id_emits_no_events(self, event_bus_async: EventBus):
        """无 utterance_id：started / finished 都不发"""
        provider = EdgeTTSProvider(config={"type": "edge_tts"}, event_bus=event_bus_async)
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        mgr = await _prepare_started_provider(provider)

        with (
            patch.object(
                provider,
                "_edge_tts_synthesize",
                new=AsyncMock(return_value=(np.zeros(48000, dtype=np.float32), 48000)),
            ),
            patch.object(provider, "audio_manager", new=mgr),
        ):
            await provider.handle_speech("你好")

        await event_bus_async.cleanup()
        assert captured == []

    async def test_utterance_id_emits_started_then_finished(self, event_bus_async: EventBus):
        """有 utterance_id：started → finished 顺序，duration_ms 一致"""
        provider = EdgeTTSProvider(config={"type": "edge_tts"}, event_bus=event_bus_async)
        captured_events: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured_events.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        mgr = await _prepare_started_provider(provider)

        with (
            patch.object(
                provider, "_edge_tts_synthesize", new=AsyncMock(return_value=(np.zeros(48000, dtype=np.float32), 48000))
            ),
            patch.object(provider, "audio_manager", new=mgr),
        ):
            await provider.handle_speech("你好", utterance_id="utt_edge_1")

        await event_bus_async.cleanup()

        # 两条事件
        assert [e[0] for e in captured_events] == [
            CoreEvents.TTS_UTTERANCE_STARTED,
            CoreEvents.TTS_UTTERANCE_FINISHED,
        ]

        started = captured_events[0][1]
        assert isinstance(started, UtteranceStartedPayload)
        assert started.utterance_id == "utt_edge_1"
        assert started.speech_text == "你好"
        assert started.engine == "edge_tts"
        assert started.duration_ms == 1000  # 48000 / 48000 * 1000

        finished = captured_events[1][1]
        assert isinstance(finished, UtteranceFinishedPayload)
        assert finished.utterance_id == "utt_edge_1"
        assert finished.engine == "edge_tts"
        assert finished.duration_ms == 1000


@pytest.mark.asyncio
class TestUtteranceEventsFailed:
    """失败路径：发布 failed 事件并 raise（编排队列兜底）"""

    async def test_synthesis_failure_emits_failed_and_raises(self, event_bus_async: EventBus):
        provider = EdgeTTSProvider(config={"type": "edge_tts"}, event_bus=event_bus_async)
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FAILED, capture, model_class=UtteranceFailedPayload)

        await _prepare_started_provider(provider)

        # 模拟 _edge_tts_synthesize 抛异常 → _synthesize 包成 RuntimeError → 向上 raise
        with patch.object(
            provider,
            "_edge_tts_synthesize",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await provider.handle_speech("你好", utterance_id="utt_fail_1")

        await event_bus_async.cleanup()

        # failed 事件被发布（started 不一定发了——_synthesize 抛在合成调用栈内部）
        failed_events = [e for e in captured if e[0] == CoreEvents.TTS_UTTERANCE_FAILED]
        assert len(failed_events) == 1
        failed = failed_events[0][1]
        assert isinstance(failed, UtteranceFailedPayload)
        assert failed.utterance_id == "utt_fail_1"
        assert failed.engine == "edge_tts"
        assert "boom" in failed.error_message


@pytest.mark.asyncio
class TestUtteranceEventsNoBus:
    """event_bus=None：不抛，按"手动/直调场景"语义静默跳过"""

    async def test_handle_speech_with_none_event_bus_succeeds(self):
        provider = EdgeTTSProvider(config={"type": "edge_tts"}, event_bus=None)

        mgr = await _prepare_started_provider(provider)

        with (
            patch.object(
                provider, "_edge_tts_synthesize", new=AsyncMock(return_value=(np.zeros(48000, dtype=np.float32), 48000))
            ),
            patch.object(provider, "audio_manager", new=mgr),
        ):
            await provider.handle_speech("hi", utterance_id="utt_no_bus")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
