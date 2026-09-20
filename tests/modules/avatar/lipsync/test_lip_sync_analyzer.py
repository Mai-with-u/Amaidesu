"""LipSyncAnalyzer 口型分析器测试

覆盖：
- 会话形状（``AudioSink`` 契约）：start → feed×N → stop，停止后嘴部信号收静止值；
- 喂正弦 PCM → 音量/张嘴信号非零（分析生效）；
- 一个分析器扇出多个渲染器（都收到信号）；
- 渲染器异常 fail-soft（不影响分析循环）；
- feed 非阻塞（float32 输入归一化，立即返回）。
"""

from __future__ import annotations

import asyncio
from typing import List

import numpy as np
import pytest

from src.modules.avatar.lipsync import LipSyncAnalyzer, MouthSignal


class _RecordingRenderer:
    """记录收到的口型信号（测试桩）"""

    def __init__(self) -> None:
        self.signals: List[MouthSignal] = []
        self.session_ended = False
        self.boom_on_signal = False

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        if self.boom_on_signal:
            raise RuntimeError("boom")
        self.signals.append(signal)

    async def on_session_end(self) -> None:
        self.session_ended = True


def _sine_pcm(freq_hz: int = 200, seconds: float = 0.5, sample_rate: int = 16000, dtype: str = "int16") -> np.ndarray:
    """正弦波 PCM（模拟有声语音）"""
    t = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False)
    wave = 0.6 * np.sin(2 * np.pi * freq_hz * t)
    if dtype == "int16":
        return (wave * 32767).astype(np.int16)
    return wave.astype(np.float32)


@pytest.mark.asyncio
async def test_session_emits_loud_signal_and_rests_on_stop():
    """喂正弦 PCM → 出现非零张嘴信号；stop+wait_finished 后收静止值。"""
    renderer = _RecordingRenderer()
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0}, renderers=[renderer])

    analyzer.start("utt_a1")
    analyzer.feed(_sine_pcm(), sample_rate=16000)
    await asyncio.sleep(0.15)  # 让分析循环跑几拍

    assert analyzer.is_speaking is True
    loud = [s for s in renderer.signals if s.mouth_open > 0.01]
    assert loud, f"应有非零张嘴信号，实际 {[ (s.mouth_open, s.volume) for s in renderer.signals[:5] ]}"
    assert loud[0].utterance_id == "utt_a1"

    analyzer.stop()
    await analyzer.wait_finished(timeout=2.0)

    # 收尾：静止信号 + 渲染器会话结束回调
    assert analyzer.last_signal.mouth_open == 0.0
    assert renderer.session_ended is True
    assert renderer.signals[-1].mouth_open == 0.0


@pytest.mark.asyncio
async def test_silence_keeps_mouth_closed():
    """喂静音 PCM → 张嘴信号保持 0（静音检测）。"""
    renderer = _RecordingRenderer()
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0}, renderers=[renderer])

    analyzer.start("utt_silence")
    analyzer.feed(np.zeros(16000, dtype=np.int16), sample_rate=16000)
    await asyncio.sleep(0.15)
    analyzer.stop()
    await analyzer.wait_finished(timeout=2.0)

    assert all(s.mouth_open == 0.0 for s in renderer.signals)


@pytest.mark.asyncio
async def test_float32_input_normalized():
    """float32 归一化输入被接受（引擎播放器 dtype 形态之一）。"""
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0})
    analyzer.start("utt_f32")
    analyzer.feed(_sine_pcm(dtype="float32"), sample_rate=16000)
    await asyncio.sleep(0.1)
    analyzer.stop()
    await analyzer.wait_finished(timeout=2.0)
    # 不抛、缓冲有数据即通过


@pytest.mark.asyncio
async def test_feed_before_start_is_noop():
    """未开会话时 feed 静默丢弃（无会话上下文）。"""
    analyzer = LipSyncAnalyzer()
    analyzer.feed(_sine_pcm(), sample_rate=16000)  # 不应抛
    assert analyzer.is_speaking is False


@pytest.mark.asyncio
async def test_renderer_exception_is_fail_soft():
    """渲染器抛异常 → 分析循环继续、其他渲染器仍收到信号（fail-soft）。"""
    broken = _RecordingRenderer()
    broken.boom_on_signal = True
    healthy = _RecordingRenderer()
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0}, renderers=[broken, healthy])

    analyzer.start("utt_boom")
    analyzer.feed(_sine_pcm(), sample_rate=16000)
    await asyncio.sleep(0.15)
    analyzer.stop()
    await analyzer.wait_finished(timeout=2.0)

    assert healthy.signals, "健康渲染器应照常收到信号"


@pytest.mark.asyncio
async def test_multiple_renderers_all_receive():
    """一个分析器扇出多具皮套渲染器（D4：不重复计算）。"""
    r1, r2 = _RecordingRenderer(), _RecordingRenderer()
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0}, renderers=[r1, r2])

    analyzer.start("utt_fanout")
    analyzer.feed(_sine_pcm(), sample_rate=16000)
    await asyncio.sleep(0.15)
    analyzer.stop()
    await analyzer.wait_finished(timeout=2.0)

    assert r1.signals and r2.signals


def test_add_remove_renderer_idempotent():
    """注册幂等；注销未注册的渲染器静默。"""
    analyzer = LipSyncAnalyzer()
    r = _RecordingRenderer()
    analyzer.add_renderer(r)
    analyzer.add_renderer(r)
    assert analyzer._renderers.count(r) == 1
    analyzer.remove_renderer(r)
    analyzer.remove_renderer(r)  # 未注册，静默
    assert r not in analyzer._renderers
