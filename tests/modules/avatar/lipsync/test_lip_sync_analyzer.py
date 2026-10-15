"""LipSyncAnalyzer：会话形状（start→feed→stop）、stop 后循环自行收尾退出、换句交接不双循环、音量驱动张嘴、静音闭嘴、多渲染器扇出与渲染器异常 fail-soft"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import numpy as np
import pytest

from src.modules.avatar.lipsync import LipSyncAnalyzer, MouthSignal


class _Recorder:
    def __init__(self, boom: bool = False) -> None:
        self.signals: List[MouthSignal] = []
        # 信号与会话结束的到达顺序（None = 一次 on_session_end）
        self.timeline: List[Optional[MouthSignal]] = []
        self.session_ended = False
        self.session_end_count = 0
        self.boom = boom

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        if self.boom:
            raise RuntimeError("boom")
        self.signals.append(signal)
        self.timeline.append(signal)

    async def on_session_end(self) -> None:
        self.session_ended = True
        self.session_end_count += 1
        self.timeline.append(None)


def _sine_pcm(seconds: float = 0.2, sample_rate: int = 16000, dtype: str = "int16") -> np.ndarray:
    """200Hz 正弦波 PCM（模拟有声语音）。"""
    t = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False)
    wave = 0.6 * np.sin(2 * np.pi * 200 * t)
    return (wave * 32767).astype(np.int16) if dtype == "int16" else wave.astype(np.float32)


async def _wait_loop_exit(analyzer: LipSyncAnalyzer, timeout: float = 0.5) -> None:
    """限时等分析循环自行退出并断言非取消。

    不用 ``wait_finished``：它超时会取消循环，而取消同样走收尾分支（静止信号 +
    on_session_end），会掩盖"stop 后循环不退出"。
    """
    task = analyzer._analysis_task
    assert task is not None
    await asyncio.wait({task}, timeout=timeout)
    if not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        pytest.fail(f"stop 后分析循环未在 {timeout}s 内自行退出")
    assert not task.cancelled(), "分析循环应自行退出而非被取消"


def _live_analysis_loops() -> List[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name().endswith("analysis_loop") and not t.done()]


async def _session(renderers: list, pcm: np.ndarray, utterance_id: str = "utt") -> LipSyncAnalyzer:
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0}, renderers=renderers)
    analyzer.start(utterance_id)
    analyzer.feed(pcm, sample_rate=16000)
    await asyncio.sleep(0.15)  # 让分析循环跑几拍
    analyzer.stop()
    await _wait_loop_exit(analyzer)
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
    await _wait_loop_exit(analyzer)
    # 收尾：静止信号 + 渲染器会话结束回调
    assert analyzer.last_signal.mouth_open == 0.0 and renderer.signals[-1].mouth_open == 0.0
    assert renderer.session_ended is True


async def test_loop_exits_after_each_stop_across_sessions() -> None:
    # 缓冲里留有超过一个分析窗口的音频时，循环也不得为它继续空转、残留循环随句数累积
    renderer = _Recorder()
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0}, renderers=[renderer])
    tasks = []
    for i in range(3):
        analyzer.start(f"utt_{i}")
        tasks.append(analyzer._analysis_task)
        analyzer.feed(_sine_pcm(seconds=0.3), sample_rate=16000)
        await asyncio.sleep(0.1)
        analyzer.stop()
        await _wait_loop_exit(analyzer)

    assert all(task.done() for task in tasks) and not _live_analysis_loops()
    assert renderer.session_end_count == 3
    # 每次收尾前一帧都是携带本句 ID 的静止信号
    rests = [renderer.timeline[i - 1] for i, item in enumerate(renderer.timeline) if item is None]
    assert [(s.utterance_id, s.mouth_open) for s in rests] == [("utt_0", 0.0), ("utt_1", 0.0), ("utt_2", 0.0)]


async def test_stop_then_immediate_start_hands_over_without_overlap() -> None:
    renderer = _Recorder()
    analyzer = LipSyncAnalyzer(config={"update_interval_ms": 1.0}, renderers=[renderer])
    analyzer.start("utt_prev")
    analyzer.feed(_sine_pcm(seconds=0.3), sample_rate=16000)
    await asyncio.sleep(0.1)
    old_task = analyzer._analysis_task

    # 同一拍内 stop 紧接 start：旧循环此刻正在 sleep，醒来时 is_speaking 已是新句的 True
    analyzer.stop()
    analyzer.start("utt_next")
    analyzer.feed(_sine_pcm(seconds=0.3), sample_rate=16000)
    await asyncio.sleep(0.15)

    assert old_task.done() and not old_task.cancelled()
    assert _live_analysis_loops() == [analyzer._analysis_task]
    # 上一句收尾（静止信号 + 会话结束）整体落在下一句首帧之前
    end_at = renderer.timeline.index(None)
    rest = renderer.timeline[end_at - 1]
    assert (rest.utterance_id, rest.mouth_open) == ("utt_prev", 0.0)
    assert all(s.utterance_id == "utt_prev" for s in renderer.timeline[:end_at])
    after = renderer.timeline[end_at + 1 :]
    assert after and all(s is not None and s.utterance_id == "utt_next" for s in after)

    analyzer.stop()
    await _wait_loop_exit(analyzer)
    assert renderer.session_end_count == 2


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
