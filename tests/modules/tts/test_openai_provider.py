"""OpenAITTSProvider 特有行为：配置硬错、流式播放顺序、失败关流、业务错误简短告警

六个引擎共同的事件契约见 test_engine_contract.py。
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, call, patch

import numpy as np
import pytest

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import (
    UtteranceFailedPayload,
    UtteranceFinishedPayload,
    is_failure_reported,
)
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


async def test_cancellation_while_waiting_for_lock_does_not_stop_active_stream() -> None:
    """等锁期被取消不得调用 stop_stream，以免误杀持锁者正在播放的语音。"""
    provider = OpenAITTSProvider(config=dict(CONFIGURED))
    mgr = await _started(provider, _stream_client())
    await provider.tts_lock.acquire()
    try:
        task = asyncio.create_task(provider.handle_speech("你好", utterance_id="utt_wait_cancel"))
        for _ in range(20):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        provider.tts_lock.release()
    mgr.stop_stream.assert_not_called()


async def test_cancellation_during_stop_stream_drain_calls_discard(event_bus) -> None:
    """收尾排空期被取消时，立即调用 stop_stream(discard_pending=True) 且不发 finished。"""
    from src.modules.events.names import CoreEvents

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    loop = asyncio.get_running_loop()
    drain_entered = asyncio.Event()
    unblock_drain = threading.Event()

    def _stop_side_effect(discard_pending: bool = False) -> None:
        if discard_pending:
            unblock_drain.set()
            return
        loop.call_soon_threadsafe(drain_entered.set)
        unblock_drain.wait(timeout=2.0)

    mgr = make_audio_manager()
    mgr.stop_stream.side_effect = _stop_side_effect
    await _started(provider, _stream_client(b"\x00\x00" * 1200), mgr)

    task = asyncio.create_task(provider.handle_speech("你好", utterance_id="utt_drain_cancel"))
    await drain_entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await event_bus.cleanup()

    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED]
    assert call(discard_pending=True) in mgr.stop_stream.call_args_list


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


# ----- HTTP 503 短退避重试与硬打断流释放 -----


def _sequenced_client(attempts: list[list[bytes] | Exception]) -> MagicMock:
    """按尝试序号依次产出预设流结果或异常的客户端替身（支持“先吐块再抛错”）。"""
    client = MagicMock()
    client.close = AsyncMock()
    index = {"n": 0}

    def _make_stream(_text: str):
        step = attempts[index["n"]]
        index["n"] += 1

        async def _gen():
            if isinstance(step, Exception):
                raise step
            for item in step:
                if isinstance(item, Exception):
                    raise item
                yield item

        return _gen()

    client.stream_synthesize = MagicMock(side_effect=_make_stream)
    return client


@pytest.mark.parametrize(
    ("busy_failures", "expected_sleeps"),
    [
        (1, [0.25]),
        (2, [0.25, 0.5]),
        (3, [0.25, 0.5, 1.0]),
        (4, [0.25, 0.5, 1.0, 1.5]),
        (5, [0.25, 0.5, 1.0, 1.5, 2.0]),
    ],
    ids=["succeed-on-2nd", "succeed-on-3rd", "succeed-on-4th", "succeed-on-5th", "succeed-on-6th"],
)
async def test_http_503_retries_with_backoff_and_succeeds(
    event_bus,
    busy_failures: int,
    expected_sleeps: list[float],
) -> None:
    """硬打断交接期遇 HTTP 503（未写出任何 PCM 块）按 5 步阶梯退避（合计 5.25s）重试，恢复后正常出声不发 failed。"""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus, sleep_fn=fake_sleep)
    captured = record_lifecycle(event_bus)
    attempts: list[list[bytes] | Exception] = [
        OpenAITTSApiError("接口返回 HTTP 503: TTS is busy, please retry later.", status_code=503)
        for _ in range(busy_failures)
    ]
    attempts.append([b"\x01\x00" * 1200])
    client = _sequenced_client(attempts)
    await _started(provider, client)

    await provider.handle_speech("你好", utterance_id="utt_503_ok")
    await event_bus.cleanup()

    assert sleeps == expected_sleeps
    assert client.stream_synthesize.call_count == busy_failures + 1
    assert provider.render_count == 1
    assert provider.error_count == 0
    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED, CoreEvents.TTS_UTTERANCE_FINISHED]
    finished = captured[1][1]
    assert isinstance(finished, UtteranceFinishedPayload) and finished.duration_ms == 50


async def test_http_503_exhausts_retries_and_emits_failed(event_bus) -> None:
    """连续 503 耗尽全部 5 次阶梯重试后走既有失败收尾：关流丢弃预缓冲、计错误、发 failed 并抛出。"""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus, sleep_fn=fake_sleep)
    captured = record_lifecycle(event_bus)
    attempts: list[list[bytes] | Exception] = [
        OpenAITTSApiError("接口返回 HTTP 503: TTS is busy", status_code=503) for _ in range(6)
    ]
    client = _sequenced_client(attempts)
    mgr = await _started(provider, client)

    with pytest.raises(OpenAITTSApiError, match="HTTP 503") as exc_info:
        await provider.handle_speech("你好", utterance_id="utt_503_exhausted")
    await event_bus.cleanup()

    assert exc_info.value.status_code == 503
    assert sleeps == [0.25, 0.5, 1.0, 1.5, 2.0]
    assert sum(sleeps) == pytest.approx(5.25)
    assert client.stream_synthesize.call_count == 6
    assert provider.error_count == 1
    mgr.stop_stream.assert_called_with(discard_pending=True)
    failed_events = [p for name, p in captured if name == CoreEvents.TTS_UTTERANCE_FAILED]
    assert len(failed_events) == 1 and isinstance(failed_events[0], UtteranceFailedPayload)


@pytest.mark.parametrize("status_code", [401, 429, 500, None])
async def test_non_503_api_error_does_not_retry(event_bus, status_code: int | None) -> None:
    """非 503 错误（或无状态码的连接错误）绝不重试，立刻走失败路径。"""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus, sleep_fn=fake_sleep)
    captured = record_lifecycle(event_bus)
    client = _sequenced_client([OpenAITTSApiError("upstream error", status_code=status_code)])
    mgr = await _started(provider, client)

    with pytest.raises(OpenAITTSApiError):
        await provider.handle_speech("你好", utterance_id="utt_non503")
    await event_bus.cleanup()

    assert sleeps == []
    assert client.stream_synthesize.call_count == 1
    assert provider.error_count == 1
    mgr.stop_stream.assert_called_with(discard_pending=True)
    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED, CoreEvents.TTS_UTTERANCE_FAILED]


async def test_http_503_after_first_pcm_chunk_does_not_retry(event_bus) -> None:
    """已向声卡写出首块 PCM（total_samples > 0）后遇 503 不得重试，防止重复播开头叠音。"""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus, sleep_fn=fake_sleep)
    captured = record_lifecycle(event_bus)
    client = _sequenced_client(
        [
            [
                b"\x01\x00" * 240,
                OpenAITTSApiError("接口返回 HTTP 503: mid-stream busy", status_code=503),  # type: ignore[list-item]
            ]
        ]
    )
    mgr = await _started(provider, client)

    with pytest.raises(OpenAITTSApiError, match="HTTP 503"):
        await provider.handle_speech("你好", utterance_id="utt_mid_503")
    await event_bus.cleanup()

    assert sleeps == []
    assert client.stream_synthesize.call_count == 1
    assert mgr.write_chunk.call_count == 1
    mgr.stop_stream.assert_called_with(discard_pending=True)
    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED, CoreEvents.TTS_UTTERANCE_FAILED]


async def test_cancellation_during_503_backoff_exits_immediately(event_bus) -> None:
    """退避等待期间收到取消必须立即退出、走取消收尾，绝不吞取消也不再重试。"""
    entered_backoff = asyncio.Event()
    never_finish = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        entered_backoff.set()
        await never_finish.wait()

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus, sleep_fn=blocking_sleep)
    captured = record_lifecycle(event_bus)
    client = _sequenced_client(
        [
            OpenAITTSApiError("接口返回 HTTP 503: busy", status_code=503),
            [b"\x01\x00" * 240],
        ]
    )
    mgr = await _started(provider, client)

    task = asyncio.create_task(provider.handle_speech("你好", utterance_id="utt_backoff_cancel"))
    await entered_backoff.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await event_bus.cleanup()

    assert client.stream_synthesize.call_count == 1
    assert provider.error_count == 0
    mgr.stop_stream.assert_called_with(discard_pending=True)
    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED]


async def test_cancellation_mid_write_closes_stream_generator_immediately() -> None:
    """写声卡期间收到硬打断取消时，handle_speech 必须显式关闭合成流生成器，不等待 GC 才释放连接。"""
    stream_closed = asyncio.Event()

    async def _slow_stream(_text: str):
        try:
            yield b"\x01\x00" * 120
            yield b"\x02\x00" * 120
        finally:
            stream_closed.set()

    client = MagicMock()
    client.close = AsyncMock()
    client.stream_synthesize = MagicMock(side_effect=_slow_stream)

    provider = OpenAITTSProvider(config=dict(CONFIGURED))
    mgr = await _started(provider, client)
    mgr.write_chunk.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await provider.handle_speech("你好", utterance_id="utt_stream_aclose")

    assert stream_closed.is_set() is True


async def test_cancellation_preserves_cancelled_error_when_stream_aclose_raises(event_bus) -> None:
    """硬打断取消期间若合成流 aclose() 抛出 RuntimeError，必须保留原始 CancelledError、不计错误、不发 failed。"""
    aclose_called = False

    class _ExplodingCloseStream:
        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            return b"\x01\x00" * 120

        async def aclose(self) -> None:
            nonlocal aclose_called
            aclose_called = True
            raise RuntimeError("half-open socket close failed")

    client = MagicMock()
    client.close = AsyncMock()
    client.stream_synthesize = MagicMock(side_effect=lambda _text: _ExplodingCloseStream())

    provider = OpenAITTSProvider(config=dict(CONFIGURED), event_bus=event_bus)
    captured = record_lifecycle(event_bus)
    mgr = await _started(provider, client)
    mgr.write_chunk.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await provider.handle_speech("你好", utterance_id="utt_aclose_raises")
    await event_bus.cleanup()

    assert aclose_called is True
    assert provider.error_count == 0
    mgr.stop_stream.assert_called_with(discard_pending=True)
    assert [name for name, _ in captured] == [CoreEvents.TTS_UTTERANCE_STARTED]
