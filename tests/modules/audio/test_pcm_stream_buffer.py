"""PcmStreamBuffer：流式持续缓冲、句中欠载显式补零防怪叫、EOS 排空与 abort 中止测试"""

from __future__ import annotations

import numpy as np
import pytest

from src.modules.audio.pcm_stream_buffer import PcmStreamBuffer


def test_continuous_buffer_survives_start_threshold_and_crosses_chunk_boundaries() -> None:
    """攒够阈值开播后队列继续持有剩余数据，多次部分拉取跨块精确 1:1 拼接。"""
    fed_to_sink: list[np.ndarray] = []
    buf = PcmStreamBuffer(sample_rate=24000, channels=1, dtype=np.int16, on_played_frames=fed_to_sink.append)

    chunk_a = np.arange(1, 4001, dtype=np.int16)
    chunk_b = np.arange(4001, 7001, dtype=np.int16)
    buf.push(chunk_a)
    assert buf.ready_to_start(prebuffer_seconds=0.1)  # 4000 >= 2400 样本

    buf.push(chunk_b)
    assert buf.buffered_frames == 7000

    pulled: list[np.ndarray] = []
    for _ in range(3):
        out = np.full((1500, 1), 77, dtype=np.int16)
        should_stop = buf.pull_into(out)
        assert should_stop is False
        pulled.append(out[:, 0].copy())

    # 3 × 1500 = 4500 帧，跨越 chunk_a 尾与 chunk_b 头，余量仍留在队列中
    assert buf.buffered_frames == 2500
    assert buf.stats().events == 0
    assert buf.stats().played_frames == 4500
    np.testing.assert_array_equal(np.concatenate(pulled), np.arange(1, 4501, dtype=np.int16))
    np.testing.assert_array_equal(np.concatenate(fed_to_sink), np.arange(1, 4501, dtype=np.int16))


def test_mid_stream_underrun_fills_explicit_zeros_and_recovers_without_dropping_stream() -> None:
    """句中队列读空时必须显式填 0 静音（不留脏值/末帧），分接同样收到静音并在补块后无缝接续。"""
    fed_to_sink: list[np.ndarray] = []
    buf = PcmStreamBuffer(sample_rate=24000, channels=1, dtype=np.int16, on_played_frames=fed_to_sink.append)

    first = np.arange(10, 610, dtype=np.int16)  # 600 帧
    buf.push(first)

    # 第 1 次回调请求 1000 帧：前 600 帧为真实音频，后 400 帧为第一次欠载
    out1 = np.full((1000, 1), 77, dtype=np.int16)
    assert buf.pull_into(out1) is False
    np.testing.assert_array_equal(out1[:600, 0], first)
    assert np.all(out1[600:, 0] == 0)

    # 第 2 次回调仍无新块：属于同一段连续欠载，不重复计次，但连续/累计帧数继续涨
    out2 = np.full((1000, 1), 77, dtype=np.int16)
    assert buf.pull_into(out2) is False
    assert np.all(out2 == 0)

    mid_stats = buf.stats()
    assert mid_stats.events == 1
    assert mid_stats.frames == 1400
    assert mid_stats.max_contiguous_frames == 1400
    assert mid_stats.played_frames == 600

    # 上游恢复推块，正常满帧拉取；随后再次读空触发第 2 次欠载事件
    second = np.arange(1000, 2200, dtype=np.int16)  # 1200 帧
    buf.push(second)

    out3 = np.full((1000, 1), 77, dtype=np.int16)
    assert buf.pull_into(out3) is False
    np.testing.assert_array_equal(out3[:, 0], second[:1000])

    out4 = np.full((500, 1), 77, dtype=np.int16)
    assert buf.pull_into(out4) is False
    np.testing.assert_array_equal(out4[:200, 0], second[1000:])
    assert np.all(out4[200:, 0] == 0)

    final_stats = buf.stats()
    assert final_stats.events == 2
    assert final_stats.frames == 1700
    assert final_stats.max_contiguous_frames == 1400
    assert final_stats.played_frames == 1800
    assert len(fed_to_sink) == 4
    # 欠载时分接收到的是包含静音零值的输出帧，使口型闭嘴而不是卡在张嘴状态
    assert np.all(fed_to_sink[1] == 0)


def test_eos_drain_pads_soundcard_tail_zero_without_inflating_sink_or_underrun() -> None:
    """EOS 收尾末块：声卡尾对齐补零防止爆音，但分接只拿真实样本且不计作欠载。"""
    fed_to_sink: list[np.ndarray] = []
    buf = PcmStreamBuffer(sample_rate=24000, channels=1, dtype=np.int16, on_played_frames=fed_to_sink.append)

    audio = np.arange(1, 501, dtype=np.int16)
    buf.push(audio)
    assert buf.mark_eos() is True

    out = np.full((1000, 1), 77, dtype=np.int16)
    should_stop = buf.pull_into(out)
    assert should_stop is True
    np.testing.assert_array_equal(out[:500, 0], audio)
    assert np.all(out[500:, 0] == 0)

    stats = buf.stats()
    assert stats.events == 0 and stats.frames == 0 and stats.played_frames == 500
    assert len(fed_to_sink) == 1
    np.testing.assert_array_equal(fed_to_sink[0], audio)


def test_abort_clears_buffer_wakes_waiters_and_drops_subsequent_pushes() -> None:
    """abort 立即清空队列、唤醒排空等待、拒绝延迟入队，并让回调输出零且立刻停流。"""
    buf = PcmStreamBuffer(sample_rate=24000, channels=1, dtype=np.int16)
    buf.push(np.ones(2000, dtype=np.int16))

    buf.abort()
    assert buf.is_aborted is True
    assert buf.buffered_frames == 0
    assert buf.wait_finished(timeout=0.0) is True
    assert buf.push(np.ones(500, dtype=np.int16)) == 0

    out = np.full((400, 1), 77, dtype=np.int16)
    assert buf.pull_into(out) is True
    assert np.all(out == 0)


@pytest.mark.parametrize(
    ("channels", "dtype", "raw", "expected_shape"),
    [
        (1, np.int16, np.array([1, 2, 3, 4], dtype=np.int16), (4, 1)),
        (1, np.float32, np.array([[0.1], [0.2], [0.3]], dtype=np.float32), (3, 1)),
        (2, np.int16, np.array([1, 2, 3, 4, 5], dtype=np.int16), (2, 2)),
        (2, np.float32, np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32), (2, 2)),
        (2, np.int16, np.array([[10], [20]], dtype=np.int16), (2, 2)),
    ],
)
def test_normalize_shapes_and_dtypes(channels, dtype, raw, expected_shape) -> None:
    """不同维度/声道/采样格式统一规整为独立二维连续数组，避免原数组被复用污染队列。"""
    fed: list[np.ndarray] = []
    buf = PcmStreamBuffer(sample_rate=24000, channels=channels, dtype=dtype, on_played_frames=fed.append)
    source = raw.copy()
    frames = buf.push(source)
    source.fill(9)  # 修改原数组不得影响缓冲内已拷贝样本

    assert frames == expected_shape[0]
    out = np.zeros(expected_shape, dtype=dtype)
    assert buf.mark_eos() is True
    assert buf.pull_into(out) is True
    if raw.ndim == 2 and raw.shape[1] == 1 and channels == 2:
        expected = np.repeat(raw.astype(dtype), 2, axis=1)
    elif raw.ndim == 1 and channels == 2:
        expected = raw[:4].reshape(2, 2).astype(dtype)
    else:
        expected = raw.reshape(expected_shape).astype(dtype)
    np.testing.assert_array_equal(out, expected)
    assert fed[0].ndim == (1 if channels == 1 else 2)
