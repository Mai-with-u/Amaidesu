"""VoiceboxProvider 测试

覆盖：
- ``output_device_name`` 字段已从 ConfigSchema 中删除
- 无 utterance_id 时不发布事件
- 有 utterance_id 时按 started → finished 顺序发布事件；全量引擎
  duration_ms 由 PCM 样本数÷采样率精确计算
- 合成失败时主动发 failed 事件并 raise（不再返回 ToolExecutionResult）
- 工具化退役：invoke / list_tools 不再存在
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.tts.voicebox_tool import VoiceboxProvider


# =============================================================================
# Fixtures
# =============================================================================


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


def _patch_audio_manager(provider: VoiceboxProvider, mgr: MagicMock | None = None) -> Any:
    """Mock 掉 audio_manager.play_audio（Voicebox 是全量引擎，播放调用一次）"""
    if mgr is None:
        mgr = MagicMock()
    mgr.play_audio = AsyncMock()
    return patch.object(provider, "audio_manager", new=mgr)


async def _prepare_started_provider(
    provider: VoiceboxProvider,
    *,
    audio_manager: MagicMock | None = None,
) -> MagicMock:
    """提前完成 setup：让 ``handle_speech`` 的自治 ensure_setup 不再覆盖 mock。

    Voicebox 的 setup 创建 ``aiohttp.ClientSession`` 和
    ``AudioDeviceManager``，两者在测试中都需要可预测行为；通过构造拦截
    提前完成 setup 避免 ``handle_speech`` 再次 setup 覆盖。
    """
    if audio_manager is None:
        audio_manager = MagicMock(play_audio=AsyncMock())
    with patch(
        "src.modules.audio.AudioDeviceManager",
        return_value=audio_manager,
    ):
        await provider.setup()
    return audio_manager


def _setup_session(provider: VoiceboxProvider) -> Any:
    """Mock _session 为"未关闭"状态，让 _synthesize 跑过 init 检查"""
    session = MagicMock()
    session.closed = False
    return patch.object(provider, "_session", new=session)


# =============================================================================
# 配置 Schema：output_device_name 已删除
# =============================================================================


class TestConfigSchemaCleanup:
    """output_device_name 字段已从 ConfigSchema 中删除"""

    def test_config_schema_does_not_contain_output_device_name(self):
        fields = VoiceboxProvider.ConfigSchema.model_fields
        assert "output_device_name" not in fields

    def test_provider_initializes_without_output_device_name_attr(self, event_bus: EventBus):
        provider = VoiceboxProvider(
            config={"type": "voicebox", "profile_id": "p1"},
            event_bus=event_bus,
        )
        assert not hasattr(provider, "output_device_name")


# =============================================================================
# 工具化退役：引擎不再实现 ToolProvider 协议
# =============================================================================


class TestDeTooled:
    def test_provider_has_no_invoke(self, event_bus: EventBus):
        provider = VoiceboxProvider(
            config={"type": "voicebox", "profile_id": "p1"},
            event_bus=event_bus,
        )
        assert not hasattr(provider, "invoke")

    def test_provider_has_no_list_tools(self, event_bus: EventBus):
        provider = VoiceboxProvider(
            config={"type": "voicebox", "profile_id": "p1"},
            event_bus=event_bus,
        )
        assert not hasattr(provider, "list_tools")


# =============================================================================
# 配置缺失 profile_id 时的失败语义
# =============================================================================


@pytest.mark.asyncio
class TestConfigRequirements:
    """Voicebox 业务前置条件：profile_id 必须设置"""

    async def test_missing_profile_id_raises_runtime_error(self, event_bus: EventBus):
        """未配置 profile_id：handle_speech 直接 raise（profile_id 是 _synthesize 的前置错误）"""
        provider = VoiceboxProvider(
            config={"type": "voicebox"},  # 不带 profile_id
            event_bus=event_bus,
        )
        await _prepare_started_provider(provider)

        # session 必须"未关闭"才能跑到 profile_id 检查
        with _setup_session(provider):
            with pytest.raises(RuntimeError, match="profile_id"):
                await provider.handle_speech("x")


# =============================================================================
# utterance 生命周期事件（全量引擎）
# =============================================================================


@pytest.mark.asyncio
class TestUtteranceEventsFullBuffer:
    """全量引擎：started 含精确 duration_ms，finished 与 started duration 一致"""

    async def test_no_utterance_id_emits_no_events(self, event_bus_async: EventBus):
        provider = VoiceboxProvider(
            config={"type": "voicebox", "profile_id": "p1", "sample_rate": 24000},
            event_bus=event_bus_async,
        )
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        await _prepare_started_provider(provider)

        sample_count = 48000
        with (
            _setup_session(provider),
            patch.object(
                provider,
                "_create_generation",
                new=AsyncMock(return_value="gen-1"),
            ),
            patch.object(provider, "_wait_for_completion", new=AsyncMock()),
            patch.object(
                provider,
                "_download_audio",
                new=AsyncMock(return_value=b"raw_pcm_payload"),
            ),
            patch(
                "src.modules.tts.wav_decoder.extract_pcm_from_wav",
                return_value=bytes(sample_count * 2),
            ),
        ):
            await provider.handle_speech("你好")

        await event_bus_async.cleanup()
        assert captured == []

    async def test_utterance_id_emits_started_then_finished_with_exact_duration(self, event_bus_async: EventBus):
        """有 utterance_id：started → finished；duration_ms 精确（48000/24000=2000ms）"""
        provider = VoiceboxProvider(
            config={"type": "voicebox", "profile_id": "p1", "sample_rate": 24000},
            event_bus=event_bus_async,
        )
        captured_events: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured_events.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        await _prepare_started_provider(provider)

        sample_count = 48000
        with (
            _setup_session(provider),
            patch.object(
                provider,
                "_create_generation",
                new=AsyncMock(return_value="gen-1"),
            ),
            patch.object(provider, "_wait_for_completion", new=AsyncMock()),
            patch.object(
                provider,
                "_download_audio",
                new=AsyncMock(return_value=b"raw_pcm_payload"),
            ),
            patch(
                "src.modules.tts.wav_decoder.extract_pcm_from_wav",
                return_value=bytes(sample_count * 2),
            ),
        ):
            await provider.handle_speech("你好", utterance_id="utt_vb_1")

        await event_bus_async.cleanup()

        # started 和 finished 各发一次；duration_ms 精确
        assert [e[0] for e in captured_events] == [
            CoreEvents.TTS_UTTERANCE_STARTED,
            CoreEvents.TTS_UTTERANCE_FINISHED,
        ]
        started = captured_events[0][1]
        assert isinstance(started, UtteranceStartedPayload)
        assert started.utterance_id == "utt_vb_1"
        assert started.speech_text == "你好"
        assert started.engine == "voicebox"
        # 全量引擎：合成完 → duration_ms=精确值（48000/24000=2000）
        assert started.duration_ms == 2000

        finished = captured_events[1][1]
        assert isinstance(finished, UtteranceFinishedPayload)
        assert finished.utterance_id == "utt_vb_1"
        assert finished.engine == "voicebox"
        assert finished.duration_ms == 2000


@pytest.mark.asyncio
class TestUtteranceEventsFailed:
    """失败路径：发布 failed 事件并 raise（编排队列兜底）"""

    async def test_generation_failure_emits_failed_and_raises(self, event_bus_async: EventBus):
        provider = VoiceboxProvider(
            config={"type": "voicebox", "profile_id": "p1"},
            event_bus=event_bus_async,
        )
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FAILED, capture, model_class=UtteranceFailedPayload)

        await _prepare_started_provider(provider)

        # _create_generation 抛异常
        with (
            _setup_session(provider),
            patch.object(
                provider,
                "_create_generation",
                new=AsyncMock(side_effect=RuntimeError("HTTP 502")),
            ),
        ):
            with pytest.raises(RuntimeError, match="HTTP 502"):
                await provider.handle_speech("你好", utterance_id="utt_fail")

        await event_bus_async.cleanup()

        failed_events = [e for e in captured if e[0] == CoreEvents.TTS_UTTERANCE_FAILED]
        assert len(failed_events) == 1
        failed = failed_events[0][1]
        assert isinstance(failed, UtteranceFailedPayload)
        assert failed.utterance_id == "utt_fail"
        assert failed.engine == "voicebox"
        assert "HTTP 502" in failed.error_message


@pytest.mark.asyncio
class TestUtteranceEventsNoBus:
    """event_bus=None：不抛，按"手动/直调场景"语义静默跳过"""

    async def test_handle_speech_with_none_event_bus_succeeds(self):
        provider = VoiceboxProvider(
            config={"type": "voicebox", "profile_id": "p1"},
            event_bus=None,
        )
        await _prepare_started_provider(provider)

        sample_count = 24000
        with (
            _setup_session(provider),
            patch.object(
                provider,
                "_create_generation",
                new=AsyncMock(return_value="gen-1"),
            ),
            patch.object(provider, "_wait_for_completion", new=AsyncMock()),
            patch.object(
                provider,
                "_download_audio",
                new=AsyncMock(return_value=b"raw_pcm_payload"),
            ),
            patch(
                "src.modules.tts.wav_decoder.extract_pcm_from_wav",
                return_value=bytes(sample_count * 2),
            ),
        ):
            await provider.handle_speech("hi", utterance_id="utt_no_bus")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
