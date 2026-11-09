"""OpenAITTSProvider 特有行为：配置硬错、流式播放顺序、失败关流、业务错误简短告警

六个引擎共同的事件契约见 test_engine_contract.py。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.modules.events.payloads.utterance import UtteranceFailedPayload, is_failure_reported
from src.modules.tts.backends.openai.client import OpenAITTSApiError
from src.modules.tts.backends.openai.provider import DTYPE, SAMPLE_RATE, OpenAITTSProvider
from tests.modules.tts.helpers import make_audio_manager, record_lifecycle, setup_with_audio_manager

CONFIGURED = {"type": "openai_tts", "api_key": "sk-test", "voice": "alloy"}


def _stream_client(*chunks: bytes, error: Exception | None = None) -> MagicMock:
    """合成替身：stream_synthesize 返回逐块吐 PCM 的异步生成器；error 非空时调用即抛。"""
    client = MagicMock()
    client.close = AsyncMock()

    async def _gen(_text: str):
        if error is not None:
            raise error
        for chunk in chunks:
            yield chunk

    client.stream_synthesize = MagicMock(side_effect=lambda text: _gen(text))
    return client


async def _started(provider: OpenAITTSProvider, client: MagicMock, mgr: MagicMock | None = None) -> MagicMock:
    mgr = await setup_with_audio_manager(provider, mgr or make_audio_manager())
    provider.client = client
    return mgr


async def test_setup_fails_without_api_key_model_or_voice() -> None:
    with pytest.raises(RuntimeError, match="api_key"):
        await OpenAITTSProvider(config={"type": "openai_tts", "voice": "alloy"}).setup()
    with pytest.raises(RuntimeError, match="model"):
        await OpenAITTSProvider(config={"type": "openai_tts", "api_key": "sk", "model": "", "voice": "alloy"}).setup()
    with pytest.raises(RuntimeError, match="voice"):
        await OpenAITTSProvider(config={"type": "openai_tts", "api_key": "sk", "voice": ""}).setup()


async def test_sample_rate_is_fixed_by_the_endpoint() -> None:
    """pcm 封装固定 24kHz：引擎不接受采样率配置，播放器按 24kHz 造。"""
    provider = OpenAITTSProvider(config={**CONFIGURED, "sample_rate": 48000})
    with patch("src.modules.audio.AudioDeviceManager") as ctor:
        await provider.setup()
    assert ctor.call_args.kwargs["sample_rate"] == SAMPLE_RATE == 24000


async def test_handle_speech_with_empty_stream_counts_one_render() -> None:
    provider = OpenAITTSProvider(config=dict(CONFIGURED))
    assert provider._has_started is False
    await _started(provider, _stream_client())
    await provider.handle_speech("你好")
    assert provider._has_started is True and provider.render_count == 1


async def test_stream_lifecycle_start_then_writes_then_stop() -> None:
    provider = OpenAITTSProvider(config=dict(CONFIGURED))
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

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
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

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
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

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    mgr = await _started(provider, _stream_client(error=OpenAITTSApiError("接口返回 HTTP 401: Incorrect API key")))

    with pytest.raises(OpenAITTSApiError):
        await provider.handle_speech("你好", utterance_id="utt_fail")
    await event_bus.cleanup()

    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(failed, UtteranceFailedPayload) and "401" in failed.error_message
    assert mgr.stop_stream.called and provider.error_count == 1


async def test_generic_synthesis_error_marks_failure_reported(event_bus) -> None:
    """通用失败分支同样先发 failed 再抛：异常带"已报告"标记，队列兜底不重复补发。"""
    from src.modules.events.names import CoreEvents

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    await _started(provider, _stream_client(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom") as raised:
        await provider.handle_speech("你好", utterance_id="utt_fail")
    await event_bus.cleanup()

    (failed,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert isinstance(failed, UtteranceFailedPayload)
    assert is_failure_reported(raised.value)


async def test_cleanup_closes_client_and_stops_stream() -> None:
    provider = OpenAITTSProvider(config=dict(CONFIGURED))
    client = _stream_client()
    mgr = await _started(provider, client)
    await provider.cleanup()

    mgr.stop_stream.assert_called()
    assert provider._has_started is False and provider.get_stats()["is_connected"] is False


def test_stats_expose_model_and_voice() -> None:
    stats = OpenAITTSProvider(config=dict(CONFIGURED)).get_stats()
    assert stats["model"] == "gpt-4o-mini-tts" and stats["voice"] == "alloy"
    assert stats["is_connected"] is False


def test_pcm_chunk_numpy_view_is_int16_one_channel() -> None:
    """pcm 字节按 int16 一维解释（单声道样本数 = ndarray.size）。"""
    pcm = b"\x01\x00\x02\x00"
    assert np.frombuffer(pcm, dtype=DTYPE).size == 2


async def test_odd_length_chunks_are_stitched_not_dropped(event_bus) -> None:
    """传输块边界切在半个样本之间时，残字节要拼到下一块，不能丢也不能整句失败。

    真实网络的 ``iter_any()`` 交付的是任意长度字节块，奇数长度的块会让
    ``np.frombuffer`` 直接抛 ValueError（buffer size must be a multiple of
    element size）。这里把 100ms（2400 样本 / 4800 字节）在奇数字节处切成
    两段，两块拼起来必须仍是 2400 个样本、时长 100ms。
    """
    from src.modules.events.names import CoreEvents
    from src.modules.events.payloads.utterance import UtteranceFinishedPayload

    pcm = b"\x00\x00" * 2400
    odd = (pcm[:2401], pcm[2401:])  # 第一块落在半个样本中间（2401 字节是奇数）
    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    mgr = await _started(provider, _stream_client(*odd))
    await provider.handle_speech("你好", utterance_id="utt_odd")
    await event_bus.cleanup()

    written = [call.args[0] for call in mgr.write_chunk.call_args_list]
    # 首块 2401 字节 → 写 1200 个样本、留 1 字节；第二块拼上后写 1200 个
    assert [int(chunk.size) for chunk in written] == [1200, 1200]
    (finished,) = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FINISHED]
    assert isinstance(finished, UtteranceFinishedPayload) and finished.duration_ms == 100
