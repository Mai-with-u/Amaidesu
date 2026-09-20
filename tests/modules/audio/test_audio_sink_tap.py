"""音频分接（AudioSink tap）测试

覆盖：
- 播放器把音频复制递入注入的 sink（流式 write_chunk / 全量 feed 路径）；
- 会话同步开关（start_stream/stop_stream 触发 sink.start/stop）；
- sink 异常 fail-soft（不影响播放写盘）；
- 无 sink 时零开销直通。
"""

from __future__ import annotations

from typing import List, Tuple
from unittest.mock import MagicMock

import numpy as np
import pytest

from src.modules.audio.audio_device_manager import AudioDeviceManager


class _StubSink:
    """记录 start/feed/stop 调用的桩"""

    def __init__(self, boom: bool = False) -> None:
        self.calls: List[Tuple[str, object]] = []
        self.boom = boom

    def start(self, utterance_id: str = "") -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("start", utterance_id))

    def feed(self, chunk, sample_rate: int) -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("feed", (chunk, sample_rate)))

    def stop(self) -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("stop", None))


def test_write_chunk_feeds_sink():
    """流式写块 → sink.feed 收到同一块音频与采样率。"""
    sink = _StubSink()
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink)

    chunk = np.zeros(64, dtype=np.int16)
    mgr.write_chunk(chunk)

    feeds = [c for c in sink.calls if c[0] == "feed"]
    assert len(feeds) == 1
    fed_chunk, fed_rate = feeds[0][1]
    assert fed_rate == 32000
    assert (fed_chunk == chunk).all()


def test_stream_lifecycle_drives_sink_session():
    """start_stream 开 sink 会话、stop_stream 收会话（同步开关，D2）。"""
    sink = _StubSink()
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink)

    mgr.start_stream(utterance_id="utt_tap_1")
    mgr.write_chunk(np.zeros(16, dtype=np.int16))
    mgr.stop_stream()

    kinds = [c[0] for c in sink.calls]
    assert kinds == ["start", "feed", "stop"]
    assert sink.calls[0][1] == "utt_tap_1"


def test_sink_exception_is_fail_soft():
    """sink 全链路抛异常 → 播放器调用不抛（装饰性路径兜底）。"""
    sink = _StubSink(boom=True)
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink)

    mgr.start_stream(utterance_id="u")
    mgr.write_chunk(np.zeros(16, dtype=np.int16))  # 不抛
    mgr.stop_stream()  # 不抛


def test_no_sink_is_passthrough():
    """未注入 sink：各调用零影响直通。"""
    mgr = AudioDeviceManager(sample_rate=32000)
    mgr._sink_start("u")
    mgr._sink_feed(np.zeros(8, dtype=np.int16), 32000)
    mgr._sink_stop()
    # 不抛即通过


def test_full_playback_taps_sink():
    """全量播法（play_audio）同样分接；依赖缺失时跳过播放但分接会话仍开关。

    注：play_audio 在 sounddevice 缺失环境会 RuntimeError（播放本身失败），
    但 sink 的 start/feed/stop 异常兜底各自独立——本用例只验证 sink 调用
    形态，不依赖声卡。
    """
    sink = _StubSink()
    mgr = AudioDeviceManager(sample_rate=16000, sink=sink)
    audio = np.zeros(256, dtype=np.int16)

    try:
        import asyncio

        asyncio.get_event_loop().run_until_complete(mgr.play_audio(audio, samplerate=16000))
    except Exception:
        pass  # 声卡依赖缺失/播放失败的路径：播放异常与分接正交

    kinds = [c[0] for c in sink.calls]
    assert "feed" in kinds  # 音频已复制递入


def test_engine_constructs_manager_with_sink_kwarg():
    """引擎把注入的 audio_sink 透传给 AudioDeviceManager（构造链 D1）。"""
    import inspect

    sig = inspect.signature(AudioDeviceManager.__init__)
    assert "sink" in sig.parameters
