"""MiniMaxTTSProvider 特有行为：配置硬错、流式播放顺序、失败关流、业务错误简短告警

五个引擎共同的事件契约见 test_engine_contract.py。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.modules.events.payloads.utterance import UtteranceFailedPayload, is_failure_reported
from src.modules.tts.backends.minimax.client import MiniMaxTTSApiError
from src.modules.tts.backends.minimax.provider import DTYPE, MiniMaxTTSProvider
from tests.modules.tts.helpers import make_audio_manager, record_lifecycle, setup_with_audio_manager

CONFIGURED = {"type": "minimax_tts", "api_key": "mk-test", "voice_id": "female-tianmei"}


def _stream_client(*chunks: bytes, error: Exception | None = None) -> MagicMock:
    """合成替身：stream_synthesize 返回逐块吐 PCM 的异步生成器；error 非空时调用即抛。"""
    client = MagicMock()

    async def _gen(_text: str):
        if error is not None:
            raise error
        for chunk in chunks:
            yield chunk

    client.stream_synthesize = MagicMock(side_effect=lambda text: _gen(text))
    return client


async def _started(provider: MiniMaxTTSProvider, client: MagicMock, mgr: MagicMock | None = None) -> MagicMock:
    mgr = await setup_with_audio_manager(provider, mgr or make_audio_manager())
    provider.client = client
    return mgr


async def test_setup_fails_without_api_key_or_voice_id() -> None:
    with pytest.raises(RuntimeError, match="api_key"):
        await MiniMaxTTSProvider(config={"type": "minimax_tts", "voice_id": "v"}).setup()
    with pytest.raises(RuntimeError, match="voice_id"):
        await MiniMaxTTSProvider(config={"type": "minimax_tts", "api_key": "mk"}).setup()


async def test_handle_speech_with_empty_stream_counts_one_render() -> None:
    provider = MiniMaxTTSProvider(config=dict(CONFIGURED))
    assert provider._has_started is False
    await _started(provider, _stream_client())
    await provider.handle_speech("你好")
    assert provider._has_started is True and provider.render_count == 1


async def test_stream_lifecycle_start_then_writes_then_stop() -> None:
    provider = MiniMaxTTSProvider(config=dict(CONFIGURED))
    mgr = make_audio_manager()
    log: list[str] = []
    mgr.open_output_stream.side_effect = lambda: log.append("open") or "声卡流"
    mgr.start_prepared_stream.side_effect = lambda stream, utterance_id="": log.append(f"start:{stream}:{utterance_id}")
    mgr.write_chunk.side_effect = lambda chunk: log.append(f"write:{len(chunk)}")
    mgr.stop_stream.side_effect = lambda *args, **kwargs: log.append("stop")
    await _started(provider, _stream_client(b"\x01\x02", b"\x03\x04", b"\x05\x06"), mgr)
    await provider.handle_speech("x", utterance_id="utt_order")

    # 先打开声卡（工作线程里），再用打开的流开始这一句，然后逐块写、收尾
    assert log == ["open", "start:声卡流:utt_order", "write:1", "write:1", "write:1", "stop"]


async def test_finished_duration_ms_computed_from_pcm_samples(event_bus) -> None:
    """finished 的 duration_ms = 写往声卡的 PCM 样本数 ÷ 采样率（事件契约要求）。"""
    from src.modules.events.names import CoreEvents
    from src.modules.events.payloads.utterance import UtteranceFinishedPayload, UtteranceStartedPayload

    provider = MiniMaxTTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    await _started(provider, _stream_client(b"\x00\x00" * 1200, b"\x00\x00" * 1200))
    # 两块各 1200 样本，共 2400 样本 @ 24kHz = 100ms
    await provider.handle_speech("你好", utterance_id="utt_duration")
    await event_bus.cleanup()

    (started, finished) = [p for name, p in captured if name != CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(started, UtteranceStartedPayload) and started.duration_ms is None
    assert isinstance(finished, UtteranceFinishedPayload) and finished.duration_ms == 100


async def test_cancellation_mid_stream_emits_no_finished_and_discards_pending(event_bus) -> None:
    """中途取消（队列看门狗超时）不发 finished：未播完的发声没有播放完成时刻。"""
    from src.modules.events.names import CoreEvents

    provider = MiniMaxTTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    mgr = await _started(provider, _stream_client(b"c1", b"c2"))
    mgr.write_chunk.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await provider.handle_speech("你好", utterance_id="utt_cancel")
    await event_bus.cleanup()

    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED]
    mgr.stop_stream.assert_called_with(discard_pending=True)


async def test_api_error_emits_failed_and_discards_pending_half_sentence(event_bus) -> None:
    """业务错误：简短告警 + failed 事件（带已报告标记）+ 关流丢弃半句预缓冲。"""
    from src.modules.events.names import CoreEvents

    provider = MiniMaxTTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    mgr = await _started(provider, _stream_client(error=MiniMaxTTSApiError("接口业务错误 1004: invalid api key")))

    with pytest.raises(MiniMaxTTSApiError):
        await provider.handle_speech("你好", utterance_id="utt_fail")
    await event_bus.cleanup()

    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(failed, UtteranceFailedPayload) and "1004" in failed.error_message
    assert mgr.stop_stream.called and provider.error_count == 1


async def test_generic_synthesis_error_marks_failure_reported(event_bus) -> None:
    """通用失败分支同样先发 failed 再抛：异常带"已报告"标记，队列兜底不重复补发。"""
    from src.modules.events.names import CoreEvents

    provider = MiniMaxTTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    await _started(provider, _stream_client(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom") as raised:
        await provider.handle_speech("你好", utterance_id="utt_fail")
    await event_bus.cleanup()

    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(failed, UtteranceFailedPayload)
    assert is_failure_reported(raised.value)


def test_stats_expose_model_and_voice_id() -> None:
    stats = MiniMaxTTSProvider(config=dict(CONFIGURED)).get_stats()
    assert stats["model"] == "speech-02-hd" and stats["voice_id"] == "female-tianmei"
    assert stats["is_connected"] is False


def test_pcm_chunk_numpy_view_is_int16_one_channel() -> None:
    """pcm 字节按 int16 一维解释（单声道样本数 = ndarray.size）。"""
    pcm = b"\x01\x00\x02\x00"
    assert np.frombuffer(pcm, dtype=DTYPE).size == 2
