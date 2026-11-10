"""句间隙检测器与句边界分接收发：合成 PCM 夹具（正弦有声段 + 静音间隙）验证检测阈值与会话形状。"""

from __future__ import annotations

import asyncio
from typing import List
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.utterance import UtteranceSentenceBoundaryPayload
from src.modules.tts.silence_watch import (
    DEFAULT_MIN_GAP_MS,
    SilenceGapDetector,
    SentenceBoundarySink,
)

SAMPLE_RATE = 24000
_PIECE_SAMPLES = int(SAMPLE_RATE * 0.05)  # 模拟播放分接的 50ms 递入粒度


def _voiced_ms(duration_ms: int, amplitude: float = 0.5) -> "np.ndarray":
    """正弦有声段（归一化幅度，帧 RMS 远高于静音阈值）。"""
    count = int(SAMPLE_RATE * duration_ms / 1000)
    t = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _silence_ms(duration_ms: int) -> "np.ndarray":
    return np.zeros(int(SAMPLE_RATE * duration_ms / 1000), dtype=np.float32)


def _utterance_pieces() -> List["np.ndarray"]:
    """开头静音 100ms + 有声 400ms + 静音 300ms + 有声 400ms + 静音 300ms（两处句间隙）。"""
    return [
        _silence_ms(100),
        _voiced_ms(400),
        _silence_ms(300),
        _voiced_ms(400),
        _silence_ms(300),
    ]


def _feed_pieces(detector: SilenceGapDetector, pieces: List["np.ndarray"]) -> List[int]:
    """按 50ms 块逐块喂入，返回检出边界的块序号表（重复块按出现次计）。"""
    crossed: List[int] = []
    block_index = 0
    for data in pieces:
        for start in range(0, len(data), _PIECE_SAMPLES):
            if detector.feed(data[start : start + _PIECE_SAMPLES], SAMPLE_RATE):
                crossed.append(block_index)
            block_index += 1
    return crossed


# ---------------- 检测器状态机 ----------------


def test_detector_fires_once_per_gap_and_not_on_leading_silence() -> None:
    detector = SilenceGapDetector()
    assert len(_feed_pieces(detector, _utterance_pieces())) == 2


def test_detector_ignores_gap_below_threshold() -> None:
    detector = SilenceGapDetector(min_gap_ms=DEFAULT_MIN_GAP_MS)
    pieces = [_voiced_ms(300), _silence_ms(150), _voiced_ms(300), _silence_ms(100)]
    assert _feed_pieces(detector, pieces) == []


def test_detector_fires_at_threshold_boundary() -> None:
    detector = SilenceGapDetector()
    pieces = [_voiced_ms(300), _silence_ms(200), _voiced_ms(100)]
    crossed = _feed_pieces(detector, pieces)
    assert len(crossed) == 1


def test_detector_reset_rearms_session() -> None:
    detector = SilenceGapDetector()
    pieces = [_voiced_ms(300), _silence_ms(300)]
    assert len(_feed_pieces(detector, pieces)) == 1
    # 不复位继续喂纯静音：同一间隙已报过，不再重复报
    assert _feed_pieces(detector, [_silence_ms(300)]) == []
    # 新发声后的新间隙无需复位即可再报（发声帧重新武装）
    assert len(_feed_pieces(detector, pieces)) == 1
    # 复位回到初始态（开头静音不误触发）后同样再报一次
    detector.reset()
    assert len(_feed_pieces(detector, pieces)) == 1


def test_detector_handles_int16_input() -> None:
    detector = SilenceGapDetector()
    voiced = (_voiced_ms(400) * 32767).astype(np.int16)
    silence = np.zeros(int(SAMPLE_RATE * 0.3), dtype=np.int16)
    assert len(_feed_pieces(detector, [voiced, silence, voiced, silence])) == 2


def test_detector_invalid_sample_rate_and_empty_chunk() -> None:
    detector = SilenceGapDetector()
    assert detector.feed(_voiced_ms(100), 0) is False
    assert detector.feed(np.empty(0), SAMPLE_RATE) is False


def test_detector_handles_multichannel() -> None:
    detector = SilenceGapDetector()
    voiced = np.stack([_voiced_ms(300), _voiced_ms(300)], axis=1)  # 交错双声道
    silence = np.zeros((int(SAMPLE_RATE * 0.3), 2), dtype=np.float32)
    assert len(_feed_pieces(detector, [voiced, silence, voiced, silence])) == 2


@pytest.mark.parametrize(
    ("min_gap_ms", "expected_count"),
    [
        (100, 1),  # 120ms 间隙 ≥ 100ms 阈值：触发
        (500, 0),  # 300ms 间隙 < 500ms 阈值：不触发
    ],
)
def test_detector_threshold_parametrized(min_gap_ms: int, expected_count: int) -> None:
    detector = SilenceGapDetector(min_gap_ms=min_gap_ms)
    pieces = [_voiced_ms(200), _silence_ms(120), _voiced_ms(200), _silence_ms(50)]
    assert len(_feed_pieces(detector, pieces)) == expected_count


# ---------------- 分接收发（SentenceBoundarySink） ----------------


class _ExplodingDetector(SilenceGapDetector):
    """恒抛异常的检测器替身：验证 feed 的 fail-soft 边界。"""

    def feed(self, samples: "np.ndarray", sample_rate: int, channels: int = 1) -> bool:
        raise RuntimeError("boom")


async def _feed_and_pump(
    bus: EventBus, sink: SentenceBoundarySink, pieces: List["np.ndarray"], *, started: bool = True
) -> None:
    if started:
        sink.start("utt_x")
    for data in pieces:
        sink.feed(data, SAMPLE_RATE)
    if started:
        sink.stop()
    # 泵几个循环拍：call_soon_threadsafe 回调创建发布任务，发布任务再向总线
    # 注册后台派发任务；随后 drained 等全部落地（发布任务本身不在总线跟踪集）
    for _ in range(3):
        await asyncio.sleep(0)
    if bus is not None:
        await bus.wait_until_drained()


async def _collect_events() -> tuple[EventBus, SentenceBoundarySink, List[UtteranceSentenceBoundaryPayload]]:
    bus = EventBus()
    received: List[UtteranceSentenceBoundaryPayload] = []

    async def capture(event_name: str, payload: UtteranceSentenceBoundaryPayload, source: str) -> None:
        received.append(payload)

    bus.on(CoreEvents.TTS_UTTERANCE_SENTENCE_BOUNDARY, capture, model_class=UtteranceSentenceBoundaryPayload)
    return bus, SentenceBoundarySink(bus), received


async def test_sink_emits_boundary_events_with_utterance_id() -> None:
    bus, sink, received = await _collect_events()
    await _feed_and_pump(bus, sink, _utterance_pieces())
    assert [(p.utterance_id, p.segment_index) for p in received] == [("utt_x", 1), ("utt_x", 2)]


async def test_sink_without_utterance_id_emits_nothing() -> None:
    bus, sink, received = await _collect_events()
    await _feed_and_pump(bus, sink, _utterance_pieces(), started=False)
    assert received == []


async def test_sink_detector_exception_does_not_break_feed() -> None:
    bus = EventBus()
    received: List[UtteranceSentenceBoundaryPayload] = []
    sink = SentenceBoundarySink(bus, detector=_ExplodingDetector())
    # 检测器恒抛异常：feed 不向播放路径传播异常，也不发事件
    await _feed_and_pump(bus, sink, _utterance_pieces())
    assert received == []


async def test_sink_stop_resets_session_state() -> None:
    bus, sink, received = await _collect_events()
    await _feed_and_pump(bus, sink, _utterance_pieces())
    assert len(received) == 2
    # stop 后迟到 feed（旧 utterance 的块）不再触发事件
    await _feed_and_pump(bus, sink, _utterance_pieces(), started=False)
    assert len(received) == 2


async def test_sink_emit_failure_is_swallowed() -> None:
    bus = AsyncMock()
    bus.emit = AsyncMock(side_effect=RuntimeError("bus down"))
    sink = SentenceBoundarySink(bus)
    await _feed_and_pump(bus, sink, [_voiced_ms(300), _silence_ms(300)])
    bus.emit.assert_awaited_once()  # 发布确有发生，异常被 _emit 自身吞掉


async def test_sink_event_bus_none_still_feeds() -> None:
    sink = SentenceBoundarySink(None)
    sink.start("utt_n")
    for data in (_voiced_ms(300), _silence_ms(300)):
        sink.feed(data, SAMPLE_RATE)  # 不抛即通过（无总线时检出也只跳过发布）
    sink.stop()
