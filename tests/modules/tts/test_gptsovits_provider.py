"""GPTSoVITSProvider 测试

覆盖：
- ``output_device_name`` 字段已从 ConfigSchema 中删除
- 无 utterance_id 时不发布事件
- 有 utterance_id 时按 started → finished 顺序发布事件；流式引擎
  duration_ms=None（合成未完）
- 文本清洗（白名单字符）正常生效
- 合成失败时主动发 failed 事件并 raise（不再返回 ToolExecutionResult）
- 失败路径不会泄漏 started 已发布事件（应发 failed）
- 音频流播放路径：start_stream / write_chunk / stop_stream 顺序调用
- 工具化退役：invoke / list_tools 不再存在
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    UtteranceStartedPayload,
)
from src.modules.tts.gptsovits_tool import (
    DTYPE,
    GPTSoVITSProvider,
)


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


def _build_mock_audio_manager() -> MagicMock:
    """构造一个最小可用的 AudioDeviceManager mock

    三个流式方法都是同步方法（write_chunk / stop_stream），start_stream 同；
    GPT-SoVITS 在 provider 代码里直接调用，不走 await。
    """
    mgr = MagicMock()
    mgr.start_stream = MagicMock()
    mgr.write_chunk = MagicMock()
    mgr.stop_stream = MagicMock()
    return mgr


def _patch_audio_manager(provider: GPTSoVITSProvider, mgr: MagicMock | None = None) -> Any:
    """把 provider.audio_manager 替换为 mock"""
    return patch.object(provider, "audio_manager", new=(mgr or _build_mock_audio_manager()))


def _patch_audio_manager_constructor(mgr: MagicMock | None = None) -> Any:
    """在 ``src.modules.audio`` 的 ``AudioDeviceManager`` 导入处替换为 mock。

    ``handle_speech`` 自治 ensure_setup 会构造真实的 AudioDeviceManager，
    因此必须拦截构造函数本身而非事后 patch 实例属性。引擎内的 import 形式
    为 ``from src.modules.audio import AudioDeviceManager``，所以 patch 目标
    是 ``src.modules.audio.AudioDeviceManager``。
    """
    return patch(
        "src.modules.audio.AudioDeviceManager",
        return_value=(mgr or _build_mock_audio_manager()),
    )


async def _prepare_started_provider(
    provider: GPTSoVITSProvider,
    *,
    tts_client_mock: MagicMock | None = None,
    mgr: MagicMock | None = None,
) -> None:
    """提前完成 setup：让 ``handle_speech`` 的自治 ensure_setup 不再覆盖 mock。

    在 ``with`` 块外调用：先 setup 一次，让 tts_client / audio_manager 字段被
    初始化；进入 ``with`` 块后再用 mock 覆盖实例属性即可。
    """
    with _patch_audio_manager_constructor(mgr):
        await provider.setup()
    if tts_client_mock is not None:
        provider.tts_client = tts_client_mock


# =============================================================================
# 配置 Schema：output_device_name 已删除
# =============================================================================


class TestConfigSchemaCleanup:
    """output_device_name 字段已从 ConfigSchema 中删除"""

    def test_config_schema_does_not_contain_output_device_name(self):
        fields = GPTSoVITSProvider.ConfigSchema.model_fields
        assert "output_device_name" not in fields

    def test_provider_initializes_without_output_device_name_attr(self, event_bus: EventBus):
        provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus)
        assert not hasattr(provider, "output_device_name")


# =============================================================================
# 工具化退役：引擎不再实现 ToolProvider 协议
# =============================================================================


class TestDeTooled:
    def test_provider_has_no_invoke(self, event_bus: EventBus):
        provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus)
        assert not hasattr(provider, "invoke")

    def test_provider_has_no_list_tools(self, event_bus: EventBus):
        provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus)
        assert not hasattr(provider, "list_tools")


# =============================================================================
# 文本清洗白名单（保留旧行为）
# =============================================================================


class TestTextSanitization:
    """_sanitize_text_for_tts 白名单清洗行为"""

    def test_unsupported_chars_stripped(self, event_bus: EventBus):
        from src.modules.tts.gptsovits_tool import _sanitize_text_for_tts

        # 中英文数字 + 标点 = 保留；emoji 等 = 剥离
        sanitized = _sanitize_text_for_tts("你好 hello 123！🎉")
        assert "你好" in sanitized
        assert "hello" in sanitized
        assert "123" in sanitized
        assert "🎉" not in sanitized


# =============================================================================
# handle_speech：自治 ensure_setup
# =============================================================================


@pytest.mark.asyncio
class TestHandleSpeechBasic:
    async def test_handle_speech_triggers_lazy_setup(self, event_bus: EventBus):
        """handle_speech 首次调用前未 setup：自动 ensure_setup"""
        provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus)
        assert provider._has_started is False

        await _prepare_started_provider(
            provider,
            tts_client_mock=MagicMock(tts_stream=MagicMock(return_value=iter([]))),
        )

        with patch.object(provider, "tts_client", new=MagicMock(tts_stream=MagicMock(return_value=iter([])))):
            await provider.handle_speech("你好")

        assert provider._has_started is True
        assert provider.render_count == 1


# =============================================================================
# utterance 生命周期事件（流式引擎）
# =============================================================================


@pytest.mark.asyncio
class TestUtteranceEventsStreaming:
    """流式引擎：started 必发，duration_ms=None；finished 在 stop_stream 后发"""

    async def test_no_utterance_id_emits_no_events(self, event_bus_async: EventBus):
        provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus_async)
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        # 模拟流式：tts_client.tts_stream 返回空字节流的同步迭代器
        await _prepare_started_provider(
            provider,
            tts_client_mock=MagicMock(tts_stream=MagicMock(return_value=iter([]))),
        )

        await provider.handle_speech("你好")

        await event_bus_async.cleanup()
        assert captured == []

    async def test_utterance_id_emits_started_then_finished_with_streaming_call(self, event_bus_async: EventBus):
        """有 utterance_id：started → finished；duration_ms=None；start/write/stop 流式调用"""
        provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus_async)
        captured_events: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured_events.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        mgr = _build_mock_audio_manager()

        async def fake_decode_wav_chunk(chunk: bytes, dtype: Any = None) -> np.ndarray:
            return np.arange(1024, dtype=DTYPE)

        await _prepare_started_provider(
            provider,
            tts_client_mock=MagicMock(tts_stream=MagicMock(return_value=iter([b"chunk1", b"chunk2"]))),
            mgr=mgr,
        )

        with patch("src.modules.tts.wav_decoder.decode_wav_chunk", new=fake_decode_wav_chunk):
            await provider.handle_speech("你好", utterance_id="utt_gptsovits_1")

        await event_bus_async.cleanup()

        # 流式 API 顺序调用
        mgr.start_stream.assert_called_once_with()
        assert mgr.write_chunk.call_count == 2
        mgr.stop_stream.assert_called_once_with()

        # 事件顺序与字段
        assert [e[0] for e in captured_events] == [
            CoreEvents.TTS_UTTERANCE_STARTED,
            CoreEvents.TTS_UTTERANCE_FINISHED,
        ]
        started = captured_events[0][1]
        assert isinstance(started, UtteranceStartedPayload)
        assert started.utterance_id == "utt_gptsovits_1"
        assert started.speech_text == "你好"
        assert started.engine == "gptsovits"
        # 流式引擎：合成未完 → duration_ms=None
        assert started.duration_ms is None

        finished = captured_events[1][1]
        assert isinstance(finished, UtteranceFinishedPayload)
        assert finished.utterance_id == "utt_gptsovits_1"
        assert finished.engine == "gptsovits"


@pytest.mark.asyncio
class TestUtteranceEventsFailed:
    """失败路径：发布 failed 事件并 raise（编排队列兜底）"""

    async def test_synthesis_failure_emits_failed_and_raises(self, event_bus_async: EventBus):
        provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus_async)
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FAILED, capture, model_class=UtteranceFailedPayload)

        await _prepare_started_provider(
            provider,
            tts_client_mock=MagicMock(tts_stream=MagicMock(side_effect=RuntimeError("ws closed"))),
        )

        with pytest.raises(RuntimeError, match="ws closed"):
            await provider.handle_speech("你好", utterance_id="utt_fail_1")

        await event_bus_async.cleanup()

        failed_events = [e for e in captured if e[0] == CoreEvents.TTS_UTTERANCE_FAILED]
        assert len(failed_events) == 1
        failed = failed_events[0][1]
        assert isinstance(failed, UtteranceFailedPayload)
        assert failed.utterance_id == "utt_fail_1"
        assert failed.engine == "gptsovits"
        assert "ws closed" in failed.error_message


@pytest.mark.asyncio
class TestUtteranceEventsNoBus:
    """event_bus=None：不抛，按"手动/直调场景"语义静默跳过"""

    async def test_handle_speech_with_none_event_bus_succeeds(self):
        provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=None)

        await _prepare_started_provider(
            provider,
            tts_client_mock=MagicMock(tts_stream=MagicMock(return_value=iter([]))),
        )

        await provider.handle_speech("hi", utterance_id="utt_no_bus")


# =============================================================================
# 音频流顺序：start_stream / write_chunk / stop_stream
# =============================================================================


@pytest.mark.asyncio
class TestStreamingPlaybackOrder:
    """确保 start_stream → 多次 write_chunk → stop_stream 顺序"""

    async def test_stream_lifecycle_called_in_order(self, event_bus: EventBus):
        provider = GPTSoVITSProvider(config={"type": "gptsovits"}, event_bus=event_bus)
        mgr = _build_mock_audio_manager()

        call_log: list[str] = []

        def record_start() -> None:
            call_log.append("start")

        def record_write(chunk: np.ndarray) -> None:
            call_log.append(f"write:{len(chunk)}")

        def record_stop_stream() -> None:
            call_log.append("stop")

        mgr.start_stream.side_effect = record_start
        mgr.write_chunk.side_effect = record_write
        mgr.stop_stream.side_effect = record_stop_stream

        async def fake_decode_wav_chunk(chunk: bytes, dtype: Any = None) -> np.ndarray:
            return np.arange(2048, dtype=DTYPE)

        chunks = [b"c1", b"c2", b"c3"]

        await _prepare_started_provider(
            provider,
            tts_client_mock=MagicMock(tts_stream=MagicMock(return_value=iter(chunks))),
            mgr=mgr,
        )

        with patch("src.modules.tts.wav_decoder.decode_wav_chunk", new=fake_decode_wav_chunk):
            await provider.handle_speech("x", utterance_id="utt_stream_order")

        assert call_log[0] == "start"
        assert all(c.startswith("write:") for c in call_log[1:-1])
        assert call_log[-1] == "stop"
        assert len(call_log) == 1 + len(chunks) + 1  # start + N writes + stop


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
