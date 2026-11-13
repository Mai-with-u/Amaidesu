"""音频分接（AudioSink tap）测试。

覆盖音频复制、会话同步、异常隔离与无 sink 的直通路径。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

import src.modules.audio.audio_device_manager as audio_device_module
from src.modules.audio.audio_device_manager import AudioDeviceManager
from tests.modules.audio.fakes import RecordingSink

# 无音频子系统的环境（如无头 CI）下依赖降级，本文件的 sd 假件挂载点不存在
pytestmark = pytest.mark.skipif(
    not audio_device_module.DEPENDENCIES_OK,
    reason="sounddevice 依赖不可用（无音频子系统环境），音频管理器走降级路径",
)


@pytest.fixture(autouse=True)
def fake_sounddevice(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """隔离硬件与播放等待，保留播放器和 sink 的真实生命周期逻辑。"""
    backend = MagicMock(spec_set=["OutputStream", "play", "stop"])
    backend.OutputStream.return_value = MagicMock(spec_set=["start", "write", "stop", "close"])
    monkeypatch.setattr(audio_device_module, "sd", backend, raising=False)
    monkeypatch.setattr(audio_device_module, "DEPENDENCIES_OK", True)
    # 只替换被测模块的引用，不修改全局 asyncio.sleep。
    monkeypatch.setattr(audio_device_module, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    return backend


def test_write_chunk_feeds_sink() -> None:
    """流式写块使 sink 收到同一块音频与采样率（分接按播放进度递入，收尾排空兑现）。"""
    sink = RecordingSink()
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink, prebuffer_seconds=0.0)
    chunk = np.zeros(64, dtype=np.int16)

    mgr.start_stream(utterance_id="feed_once")
    mgr.write_chunk(chunk)
    mgr.stop_stream()

    feeds = [c for c in sink.calls if c[0] == "feed"]
    assert len(feeds) == 1
    fed_chunk, fed_rate = feeds[0][1]
    assert fed_rate == 32000
    assert (fed_chunk == chunk).all()


def test_stream_lifecycle_drives_sink_session(fake_sounddevice: MagicMock) -> None:
    """播放和分接会话同步开关，不依赖真实声卡。"""
    sink = RecordingSink()
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink)
    chunk = np.zeros(16, dtype=np.int16)

    mgr.start_stream(utterance_id="utt_tap_1")
    assert mgr.is_playing
    mgr.write_chunk(chunk)
    mgr.stop_stream()

    assert [c[0] for c in sink.calls] == ["start", "feed", "stop"]
    assert sink.calls[0][1] == "utt_tap_1"
    fake_sounddevice.OutputStream.assert_called_once_with(samplerate=32000, channels=1, dtype=np.int16, device=None)
    stream = fake_sounddevice.OutputStream.return_value
    assert [c[0] for c in stream.method_calls] == ["start", "write", "stop", "close"]
    # 写声卡按小片交替执行（分接跟随播放节奏），写盘内容与原块逐样本一致
    np.testing.assert_array_equal(stream.write.call_args.args[0], chunk)
    assert not mgr.is_playing
    assert mgr._stream is None


def test_sink_exception_is_fail_soft(fake_sounddevice: MagicMock) -> None:
    """sink 全链路抛异常时，播放生命周期仍能完成。"""
    sink = RecordingSink(boom=True)
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink)

    mgr.start_stream(utterance_id="u")
    mgr.write_chunk(np.zeros(16, dtype=np.int16))
    mgr.stop_stream()

    stream = fake_sounddevice.OutputStream.return_value
    assert [c[0] for c in stream.method_calls] == ["start", "write", "stop", "close"]


def test_no_sink_is_passthrough() -> None:
    """未注入 sink 时，各分接调用不影响直通路径。"""
    mgr = AudioDeviceManager(sample_rate=32000)
    mgr._sink_start("u")
    mgr._sink_feed(np.zeros(8, dtype=np.int16), 32000)
    mgr._sink_stop()


async def test_full_playback_taps_sink(fake_sounddevice: MagicMock) -> None:
    """全量播法分接：按播放节奏逐片递入（256 样本 < 一片 800，单块整体递入）。"""
    sink = RecordingSink()
    mgr = AudioDeviceManager(sample_rate=16000, sink=sink)
    audio = np.zeros(256, dtype=np.int16)

    await mgr.play_audio(audio, samplerate=16000)

    assert [c[0] for c in sink.calls] == ["start", "feed", "stop"]
    fed_chunk, fed_rate = sink.calls[1][1]
    np.testing.assert_array_equal(fed_chunk, audio)
    assert fed_rate == 16000
    fake_sounddevice.play.assert_called_once_with(audio, samplerate=16000, device=None)
    awaited = [call.args[0] for call in audio_device_module.asyncio.sleep.await_args_list]
    assert awaited == [0.05, 0.3]  # 一片 0.05s + 收尾余量 0.3s
    assert not mgr.is_playing


def test_failed_stream_creation_does_not_start_sink(fake_sounddevice: MagicMock) -> None:
    """设备初始化失败时清理分接，不把失败路径当作成功播放。"""
    fake_sounddevice.OutputStream.side_effect = RuntimeError("no output device")
    sink = RecordingSink()
    mgr = AudioDeviceManager(sample_rate=32000, sink=sink)

    mgr.start_stream(utterance_id="failed")
    mgr.stop_stream()

    assert mgr._stream is None
    assert not mgr.is_playing
    assert sink.calls == [("stop", None)]


def test_engine_constructs_manager_with_sink_kwarg() -> None:
    """管理器构造器接收音频分接依赖。"""
    assert "sink" in inspect.signature(AudioDeviceManager.__init__).parameters
