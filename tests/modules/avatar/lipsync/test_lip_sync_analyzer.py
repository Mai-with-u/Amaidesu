"""LipSyncAnalyzer：会话形状（start→feed→stop）、音量驱动张嘴、静音闭嘴、多渲染器扇出与渲染器异常 fail-soft"""

from __future__ import annotations

import asyncio
from typing import List

import numpy as np

from src.modules.avatar.lipsync import LipSyncAnalyzer, MouthSignal


class _Recorder:
    def __init__(self, boom: bool = False) -> None:
        self.signals: List[MouthSignal] = []
        self.session_ended = False
        self.boom = boom

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        if self.boom:
            raise RuntimeError("boom")
        self.signals.append(signal)

    async def on_session_end(self) -> None:
        self.session_ended = True


def _sine_pcm(seconds: float = 0.2, sample_rate: int = 16000, dtype: str = "int16") -> np.ndarray:
    """200Hz 正弦波 PCM（模拟有声语音）。"""
    t = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False)
    wave = 0.6 * np.sin(2 * np.pi * 200 * t)
    return (wave * 32767).astype(np.int16) if dtype == "int16" else wave.astype(np.float32)


async def _session(renderers: list, pcm: np.ndarray, utterance_id: str = "utt") -> LipSyncAnalyzer:
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0}, renderers=renderers)
    analyzer.start(utterance_id)
    analyzer.feed(pcm, sample_rate=16000)
    await asyncio.sleep(0.15)  # 让分析循环跑几拍
    analyzer.stop()
    await analyzer.wait_finished(timeout=2.0)
    return analyzer


async def test_voiced_pcm_opens_mouth_and_session_end_rests_it() -> None:
    renderer = _Recorder()
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0}, renderers=[renderer])
    analyzer.start("utt_a1")
    analyzer.feed(_sine_pcm(), sample_rate=16000)
    await asyncio.sleep(0.15)
    assert analyzer.is_speaking is True
    loud = [s for s in renderer.signals if s.mouth_open > 0.01]
    assert loud and loud[0].utterance_id == "utt_a1"

    analyzer.stop()
    await analyzer.wait_finished(timeout=2.0)
    # 收尾：静止信号 + 渲染器会话结束回调
    assert analyzer.last_signal.mouth_open == 0.0 and renderer.signals[-1].mouth_open == 0.0
    assert renderer.session_ended is True


async def test_silence_keeps_mouth_closed_and_float32_input_is_accepted() -> None:
    silent = _Recorder()
    await _session([silent], np.zeros(16000, dtype=np.int16))
    assert all(s.mouth_open == 0.0 for s in silent.signals)

    voiced = _Recorder()
    await _session([voiced], _sine_pcm(dtype="float32"))  # 引擎播放器 dtype 形态之一：已归一化 float32
    assert any(s.mouth_open > 0.01 for s in voiced.signals)


async def test_feed_before_start_is_dropped_silently() -> None:
    analyzer = LipSyncAnalyzer()
    analyzer.feed(_sine_pcm(), sample_rate=16000)  # 无会话上下文
    assert analyzer.is_speaking is False


async def test_one_analyzer_fans_out_to_every_renderer_and_survives_a_broken_one() -> None:
    broken, healthy, another = _Recorder(boom=True), _Recorder(), _Recorder()
    await _session([broken, healthy, another], _sine_pcm(), "utt_fanout")
    assert healthy.signals and another.signals  # 一次分析扇出多个渲染器；其一抛异常不影响分析循环与其他渲染器


def test_add_remove_renderer_is_idempotent() -> None:
    analyzer = LipSyncAnalyzer()
    renderer = _Recorder()
    analyzer.add_renderer(renderer)
    analyzer.add_renderer(renderer)
    assert analyzer._renderers.count(renderer) == 1
    analyzer.remove_renderer(renderer)
    analyzer.remove_renderer(renderer)  # 未注册时静默
    assert renderer not in analyzer._renderers
