"""OmniTTSProvider 测试

覆盖：
- ``output_device_name`` / ``use_vts_lip_sync`` / ``use_subtitle`` 字段已从
  ConfigSchema 中删除
- 无 utterance_id 时不发布事件
- 有 utterance_id 时按 started → finished 顺序发布事件；流式引擎
  duration_ms=None（合成未完）
- 修复后播放路径：start_stream → 多次 write_chunk → stop_stream（之前是
  解码后只缓冲到 deque，从未真正播放）
- 合成失败时主动发 failed 事件并 raise（不再返回 ToolExecutionResult）
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
from src.modules.tts.omni_tts_tool import OmniTTSProvider


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
    """构造一个最小可用的 AudioDeviceManager mock"""
    mgr = MagicMock()
    mgr.start_stream = MagicMock()
    mgr.write_chunk = MagicMock()
    mgr.stop_stream = MagicMock()
    return mgr


def _patch_audio_manager(provider: OmniTTSProvider, mgr: MagicMock | None = None) -> Any:
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
    provider: OmniTTSProvider,
    *,
    mgr: MagicMock | None = None,
) -> None:
    """提前完成 setup：让 ``handle_speech`` 的自治 ensure_setup 不再覆盖 mock。

    进入 ``with`` 块前调用一次 setup，让 audio_manager 字段被初始化；
    测试随后直接覆盖或保留作为 mock。
    """
    with _patch_audio_manager_constructor(mgr):
        await provider.setup()


# =============================================================================
# 配置 Schema：3 个死字段已删除
# =============================================================================


class TestConfigSchemaCleanup:
    """output_device_name / use_vts_lip_sync / use_subtitle 字段已从 ConfigSchema 中删除"""

    def test_config_schema_does_not_contain_output_device_name(self):
        fields = OmniTTSProvider.ConfigSchema.model_fields
        assert "output_device_name" not in fields

    def test_config_schema_does_not_contain_use_vts_lip_sync(self):
        fields = OmniTTSProvider.ConfigSchema.model_fields
        assert "use_vts_lip_sync" not in fields

    def test_config_schema_does_not_contain_use_subtitle(self):
        fields = OmniTTSProvider.ConfigSchema.model_fields
        assert "use_subtitle" not in fields

    def test_provider_has_no_runtime_attrs(self, event_bus: EventBus):
        """Provider 实例不应再有 output_device_name / use_vts_lip_sync / use_subtitle"""
        provider = OmniTTSProvider(config={"type": "omni_tts"}, event_bus=event_bus)
        assert not hasattr(provider, "output_device_name")
        assert not hasattr(provider, "use_vts_lip_sync")
        assert not hasattr(provider, "use_subtitle")


# =============================================================================
# 工具化退役：引擎不再实现 ToolProvider 协议
# =============================================================================


class TestDeTooled:
    def test_provider_has_no_invoke(self, event_bus: EventBus):
        provider = OmniTTSProvider(config={"type": "omni_tts"}, event_bus=event_bus)
        assert not hasattr(provider, "invoke")

    def test_provider_has_no_list_tools(self, event_bus: EventBus):
        provider = OmniTTSProvider(config={"type": "omni_tts"}, event_bus=event_bus)
        assert not hasattr(provider, "list_tools")


# =============================================================================
# 修复后的播放链路：start_stream / write_chunk / stop_stream
# =============================================================================


@pytest.mark.asyncio
class TestStreamingPlayback:
    """本次改造核心：解码后真正出声

    修复前：``_synthesize`` 把解码缓冲到 ``audio_data_queue`` 但从未播放
    修复后：``_synthesize`` 按 start_stream → write_chunk → stop_stream 顺序
            把每块 PCM 写给 AudioDeviceManager
    """

    async def test_chunks_are_written_to_audio_manager(self, event_bus: EventBus):
        """每个 HTTP 块经 ``_decode_to_pcm`` 后调用 ``audio_manager.write_chunk``"""
        provider = OmniTTSProvider(
            config={"type": "omni_tts", "ref_audio_path": "/tmp/ref.wav"},
            event_bus=event_bus,
        )
        mgr = _build_mock_audio_manager()
        await _prepare_started_provider(provider, mgr=mgr)

        # 模拟 _tts_stream 返回 2 块 raw bytes
        with (
            patch.object(
                provider,
                "_tts_stream",
                return_value=iter([b"raw_chunk_1", b"raw_chunk_2", b""]),
            ),
        ):
            # _decode_to_pcm 返回固定长度的 PCM ndarray
            with patch.object(
                provider,
                "_decode_to_pcm",
                return_value=np.arange(1024, dtype=np.int16),
            ) as mock_decode:
                await provider.handle_speech("你好", utterance_id="utt_omni_1")

        # 跳过空块 + 调用 2 次 write_chunk
        mgr.start_stream.assert_called_once_with()
        assert mgr.write_chunk.call_count == 2
        mgr.stop_stream.assert_called_once_with()
        assert mock_decode.call_count == 2

    async def test_empty_chunk_skips_decode(self, event_bus: EventBus):
        """空字节块短路跳过（与未修复前的 input_pcm_queue 残留无关）"""
        provider = OmniTTSProvider(
            config={"type": "omni_tts", "ref_audio_path": "/tmp/ref.wav"},
            event_bus=event_bus,
        )
        mgr = _build_mock_audio_manager()
        await _prepare_started_provider(provider, mgr=mgr)

        with patch.object(
            provider,
            "_tts_stream",
            return_value=iter([b"", b"", b""]),
        ):
            with patch.object(provider, "_decode_to_pcm") as mock_decode:
                await provider.handle_speech("x", utterance_id="utt_omni_empty")

        mock_decode.assert_not_called()
        mgr.start_stream.assert_called_once_with()
        mgr.stop_stream.assert_called_once_with()


# =============================================================================
# utterance 生命周期事件（流式引擎）
# =============================================================================


@pytest.mark.asyncio
class TestUtteranceEventsStreaming:
    """OmniTTS 流式引擎：started 必发，duration_ms=None；finished 在 stop_stream 后发"""

    async def test_no_utterance_id_emits_no_events(self, event_bus_async: EventBus):
        provider = OmniTTSProvider(
            config={"type": "omni_tts", "ref_audio_path": "/tmp/ref.wav"},
            event_bus=event_bus_async,
        )
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        await _prepare_started_provider(provider)

        with patch.object(provider, "_tts_stream", return_value=iter([])):
            await provider.handle_speech("你好")

        await event_bus_async.cleanup()
        assert captured == []

    async def test_utterance_id_emits_started_then_finished(self, event_bus_async: EventBus):
        """有 utterance_id：started → finished；duration_ms=None"""
        provider = OmniTTSProvider(
            config={"type": "omni_tts", "ref_audio_path": "/tmp/ref.wav"},
            event_bus=event_bus_async,
        )
        captured_events: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured_events.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FINISHED, capture, model_class=UtteranceFinishedPayload)

        await _prepare_started_provider(provider)

        with (
            patch.object(provider, "_tts_stream", return_value=iter([b"chunk1"])),
            patch.object(provider, "_decode_to_pcm", return_value=np.zeros(1024, dtype=np.int16)),
        ):
            await provider.handle_speech("你好", utterance_id="utt_omni_evt")

        await event_bus_async.cleanup()

        assert [e[0] for e in captured_events] == [
            CoreEvents.TTS_UTTERANCE_STARTED,
            CoreEvents.TTS_UTTERANCE_FINISHED,
        ]
        started = captured_events[0][1]
        assert isinstance(started, UtteranceStartedPayload)
        assert started.utterance_id == "utt_omni_evt"
        assert started.speech_text == "你好"
        assert started.engine == "omni_tts"
        # 流式引擎：合成未完 → duration_ms=None
        assert started.duration_ms is None

        finished = captured_events[1][1]
        assert isinstance(finished, UtteranceFinishedPayload)
        assert finished.utterance_id == "utt_omni_evt"
        assert finished.engine == "omni_tts"


@pytest.mark.asyncio
class TestUtteranceEventsFailed:
    """失败路径：发布 failed 事件并 raise（编排队列兜底）

    失败时音频流应被停止，避免泄漏 OutputStream 资源。
    """

    async def test_synthesis_failure_emits_failed_and_stops_stream(self, event_bus_async: EventBus):
        provider = OmniTTSProvider(
            config={"type": "omni_tts", "ref_audio_path": "/tmp/ref.wav"},
            event_bus=event_bus_async,
        )
        captured: list[tuple[str, object]] = []

        async def capture(event_name: str, data: object, source: str) -> None:
            captured.append((event_name, data))

        event_bus_async.on(CoreEvents.TTS_UTTERANCE_STARTED, capture, model_class=UtteranceStartedPayload)
        event_bus_async.on(CoreEvents.TTS_UTTERANCE_FAILED, capture, model_class=UtteranceFailedPayload)

        mgr = _build_mock_audio_manager()
        await _prepare_started_provider(provider, mgr=mgr)

        # _tts_stream 抛异常
        with patch.object(provider, "_tts_stream", side_effect=RuntimeError("network down")):
            with pytest.raises(RuntimeError, match="network down"):
                await provider.handle_speech("你好", utterance_id="utt_fail")

        await event_bus_async.cleanup()

        # 流已被停止（异常路径清理）
        mgr.stop_stream.assert_called()

        failed_events = [e for e in captured if e[0] == CoreEvents.TTS_UTTERANCE_FAILED]
        assert len(failed_events) == 1
        failed = failed_events[0][1]
        assert isinstance(failed, UtteranceFailedPayload)
        assert failed.utterance_id == "utt_fail"
        assert failed.engine == "omni_tts"
        assert "network down" in failed.error_message


@pytest.mark.asyncio
class TestUtteranceEventsNoBus:
    """event_bus=None：不抛，按"手动/直调场景"语义静默跳过"""

    async def test_handle_speech_with_none_event_bus_succeeds(self):
        provider = OmniTTSProvider(
            config={"type": "omni_tts", "ref_audio_path": "/tmp/ref.wav"},
            event_bus=None,
        )
        await _prepare_started_provider(provider)

        with patch.object(provider, "_tts_stream", return_value=iter([])):
            await provider.handle_speech("hi", utterance_id="utt_no_bus")


# =============================================================================
# 配置缺失 ref_audio_path 时的失败语义
# =============================================================================


@pytest.mark.asyncio
class TestConfigRequirements:
    """OmniTTS 业务前置条件：参考音频省略时走服务端默认"""

    async def test_missing_ref_audio_omits_reference_params(self, event_bus: EventBus):
        """未设置 ref_audio_path：请求参数省略参考音频三键（服务端用默认）"""
        provider = OmniTTSProvider(config={"type": "omni_tts"}, event_bus=event_bus)

        captured_params: dict = {}

        def _capture_get(url, params=None, **kwargs):
            captured_params.update(params or {})
            raise ConnectionError("diagnostic stop")

        with patch("src.modules.tts.omni_tts_tool.requests.get", side_effect=_capture_get):
            await _prepare_started_provider(provider)
            with pytest.raises(ConnectionError, match="diagnostic stop"):
                await provider.handle_speech("x")

        assert "ref_audio_path" not in captured_params
        assert "prompt_text" not in captured_params
        assert captured_params.get("text") == "x"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
