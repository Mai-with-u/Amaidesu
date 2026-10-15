"""口型幅度链路回归：以 GPT-SoVITS 实况形态（32kHz int16 流式逐块、真实节奏）喂分析器

失败形态即用户症状：峰值远低于 0.35（幅度小）、句中反复"闭合→重开"振荡（抽搐回初始状态）。
"""

import asyncio
from typing import List

import numpy as np

from src.modules.avatar.lipsync import LipSyncAnalyzer, LipSyncRenderer, MouthSignal

# 实测配置（config/avatar.toml [lipsync]）；sample_rate 是分析器配置值，GPT-SoVITS 实际喂入率另算
USER_LIPSYNC_CONFIG = {
    "enabled": True,
    "sample_rate": 16000,
    "volume_threshold": 0.01,
    "smoothing_factor": 0.3,
    "vowel_detection_sensitivity": 0.5,
    "volume_gain": 1.0,
    "max_mouth_open": 0.6,
    "silence_threshold": 0.02,
    "close_mouth_threshold": 0.06,
    "power_curve": 1.0,
    "vowel_open_weight": 0.5,
    "update_interval_ms": 30.0,
    "mouth_open_lerp_speed": 0.35,
    "vowel_decay": 0.4,
    "min_mouth_delta": 0.005,
}
REAL_FEED_RATE = 32000  # gptsovits Schema 默认，流式 write_chunk 实际喂入率


def _speech_like_audio(sample_rate: int, target_rms: float = 0.07) -> np.ndarray:
    """6 个音节（浊音 180ms + 间隙 120ms），中段一处 350ms 句读停顿；浊音为共振峰附近的双正弦，RMS 校准到典型 TTS 响度。"""
    segments: List[tuple[float, str]] = []
    for i in range(6):
        segments.append((0.18, "voiced"))
        if i == 2:
            segments.append((0.35, "pause"))
        elif i < 5:
            segments.append((0.12, "gap"))

    pieces: List[np.ndarray] = []
    for duration, kind in segments:
        n = int(duration * sample_rate)
        t = np.arange(n) / sample_rate
        if kind == "voiced":
            wave = 0.6 * np.sin(2 * np.pi * 700.0 * t) + 0.4 * np.sin(2 * np.pi * 1200.0 * t)
            pieces.append(wave * np.sin(np.linspace(0.0, np.pi, n)) ** 0.5)  # 音节自然起收
        else:
            pieces.append(np.zeros(n))
    audio = np.concatenate(pieces)
    voiced = np.abs(audio) > 1e-6
    audio[voiced] *= target_rms / float(np.sqrt(np.mean(audio[voiced] ** 2)))  # 只对浊音段归一，静音段保持 0
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)


class _Recorder(LipSyncRenderer):
    def __init__(self) -> None:
        self.mouth: List[float] = []

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        self.mouth.append(signal.mouth_open)


async def _run(analyzer_sample_rate: int) -> List[float]:
    """按实况节奏（20ms 块）喂一段合成语音，返回张嘴轨迹。"""
    analyzer = LipSyncAnalyzer(config={**USER_LIPSYNC_CONFIG, "sample_rate": analyzer_sample_rate})
    recorder = _Recorder()
    analyzer.add_renderer(recorder)
    audio = _speech_like_audio(REAL_FEED_RATE)
    chunk = int(0.02 * REAL_FEED_RATE)
    analyzer.start("utt-harness")
    for i in range(0, len(audio), chunk):
        analyzer.feed(audio[i : i + chunk], REAL_FEED_RATE)
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.15)  # 播放尾部的实时余量
    analyzer.stop()
    await analyzer.wait_finished(timeout=3.0)
    return recorder.mouth


def _twitch_count(mouth: List[float]) -> int:
    """统计 >0.15 → <0.08 → >0.15 的完整闭合回弹次数。"""
    state, count = 0, 0  # 0=未张 1=张开 2=已闭合待重开
    for value in mouth:
        if state == 0 and value > 0.15:
            state = 1
        elif state == 1 and value < 0.08:
            state = 2
        elif state == 2 and value > 0.15:
            count, state = count + 1, 1
    return count


async def test_normal_loudness_speech_is_visible_without_mid_sentence_twitch_and_real_rate_is_no_worse() -> None:
    mismatched = await _run(16000)  # 配置采样率与实际喂入率错配
    matched = await _run(REAL_FEED_RATE)

    assert max(mismatched) >= 0.35, f"口型幅度过小：峰值 {max(mismatched):.3f} < 0.35（max_mouth_open=0.6 的可见下限）"
    assert _twitch_count(mismatched) <= 1  # 句读停顿允许一次，句中不应反复闭嘴回弹
    assert max(matched) >= max(mismatched) - 0.02  # 使用真实采样率不应更差
