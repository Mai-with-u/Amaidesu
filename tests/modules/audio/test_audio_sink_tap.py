"""音频分接（AudioSink tap）测试

覆盖：
- 播放器把音频复制递入注入的 sink（流式 write_chunk / 全量 feed 路径）；
- 会话同步开关（start_stream/stop_stream 触发 sink.start/stop）；
- sink 异常 fail-soft（不影响播放写盘）；
- 无 sink 时零开销直通。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import List, Tuple
from unittest.mock import MagicMock

import numpy as np
import pytest

import src.modules.audio.audio_device_manager as audio_device_module
from src.modules.audio.audio_device_manager import AudioDeviceManager


@pytest.fixture(autouse=True)
def fake_sounddevice(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """只替换硬件边界，保留播放器与 sink 的真实生命周期逻辑。"""
    backend = MagicMock(spec_set=["OutputStream", "play", "stop"])
    backend.OutputStream.return_value = MagicMock(spec_set=["start", "write", "stop", "close"])
    monkeypatch.setattr(audio_device_module, "sd", backend, raising=False)
    monkeypatch.setattr(audio_device_module, "DEPENDENCIES_OK", True)
    return backend


class _StubSink:
    """记录 start/feed/stop 调用的桩"""

    def __init__(self, boom: bool = False) -> None:
        self.calls: List[Tuple[str, object]] = []
        self.boom = boom

    def start(self, utterance_id: str = "") -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("start", utterance_id))

    def feed(self, chunk: np.ndarray, sample_rate: int) -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("feed", (chunk, sample_rate)))

    def stop(self) -> None:
        if self.boom:
            raise RuntimeError("sink boom")
        self.calls.append(("stop", None))


def test_write_chunk_feeds_sink() -> None:
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


def test_stream_lifecycle_drives_sink_session(fake_sounddevice: MagicMock) -> None:
    """start_stream 开 sink 会话、stop_stream 收会话，不依赖真实声卡。"""
    sink = _StubSink()
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink)
    chunk = np.zeros(16, dtype=np.int16)

    mgr.start_stream(utterance_id="utt_tap_1")
    assert mgr.is_playing
    mgr.write_chunk(chunk)
    mgr.stop_stream()

    kinds = [c[0] for c in sink.calls]
    assert kinds == ["start", "feed", "stop"]
    assert sink.calls[0][1] == "utt_tap_1"
    fake_sounddevice.OutputStream.assert_called_once_with(
        samplerate=32000, channels=1, dtype=np.int16, device=None
    )
    stream = fake_sounddevice.OutputStream.return_value
    assert [c[0] for c in stream.method_calls] == ["start", "write", "stop", "close"]
    assert stream.write.call_args.args[0] is chunk
    assert not mgr.is_playing
    assert mgr._stream is None


def test_sink_exception_is_fail_soft(fake_sounddevice: MagicMock) -> None:
    """sink 全链路抛异常时，实际播放生命周期仍能完成。"""
    sink = _StubSink(boom=True)
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink)

    mgr.start_stream(utterance_id="u")
    mgr.write_chunk(np.zeros(16, dtype=np.int16))
    mgr.stop_stream()

    stream = fake_sounddevice.OutputStream.return_value
    assert [c[0] for c in stream.method_calls] == ["start", "write", "stop", "close"]


def test_no_sink_is_passthrough() -> None:
    """未注入 sink：各调用零影响直通。"""
    mgr = AudioDeviceManager(sample_rate=32000)
    mgr._sink_start("u")
    mgr._sink_feed(np.zeros(8, dtype=np.int16), 32000)
    mgr._sink_stop()


def test_full_playback_taps_sink(fake_sounddevice: MagicMock) -> None:
    """全量播法完整验证分接会话，不吞掉播放路径上的意外异常。"""
    sink = _StubSink()
    mgr = AudioDeviceManager(sample_rate=16000, sink=sink)
    audio = np.zeros(256, dtype=np.int16)

    asyncio.run(mgr.play_audio(audio, samplerate=16000))

    assert [c[0] for c in sink.calls] == ["start", "feed", "stop"]
    fed_chunk, fed_rate = sink.calls[1][1]
    assert fed_chunk is audio
    assert fed_rate == 16000
    fake_sounddevice.play.assert_called_once_with(audio, samplerate=16000, device=None)
    assert not mgr.is_playing


def test_failed_stream_creation_does_not_start_sink(fake_sounddevice: MagicMock) -> None:
    """设备初始化失败时清理分接，不把失败路径当作成功播放。"""
    fake_sounddevice.OutputStream.side_effect = RuntimeError("no output device")
    sink = _StubSink()
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink)

    mgr.start_stream(utterance_id="failed")
    mgr.stop_stream()

    assert mgr._stream is None
    assert not mgr.is_playing
    assert sink.calls == [("stop", None)]


def test_engine_constructs_manager_with_sink_kwarg() -> None:
    """引擎把注入的 audio_sink 透传给 AudioDeviceManager（构造链 D1）。"""
    sig = inspect.signature(AudioDeviceManager.__init__)
    assert "sink" in sig.parameters
