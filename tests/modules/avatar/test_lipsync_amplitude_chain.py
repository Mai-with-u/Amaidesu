"""口型幅度链路回归测试（复现"幅度很小 + 抽搐回闭"症状）

反馈环：以 GPT-SoVITS 实况形态（32kHz int16 流式逐块、真实时间节奏、
音节能量包络 + 段间停顿）喂 LipSyncAnalyzer，录制 MouthSignal 轨迹，
断言正常响度语音（RMS≈0.07）应产生可见口型（峰值 ≥ 0.35）且句中
不应反复闭嘴回弹。

失败形态即用户症状：峰值远低于 0.35（幅度小）、句中出现多次
"闭合→重开"振荡（抽搐回初始状态）。
"""

import asyncio
from typing import List

import numpy as np
import pytest

from src.modules.avatar.lipsync import LipSyncAnalyzer, LipSyncRenderer, MouthSignal

# ---------------------------------------------------------------------------
# 用户 config/avatar.toml [lipsync] 的现值（2026-09-22 实测配置）
# ---------------------------------------------------------------------------

USER_LIPSYNC_CONFIG = {
    "enabled": True,
    "sample_rate": 16000,  # 实际 GPT-SoVITS 喂入 32000
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


def _make_speech_like_audio(sample_rate: int, target_rms: float = 0.07) -> np.ndarray:
    """合成"GPT-SoVITS 形态"语音：6 个音节（浊音段 180ms + 间隙 120ms）、
    中段一处 350ms 句读停顿，浊音段为共振峰附近双正弦叠加，
    整体 RMS 校准到 target_rms（典型 TTS 输出响度量级）。"""
    syllable_voiced = 0.18
    syllable_gap = 0.12
    pause = 0.35
    segments: List[tuple[float, str]] = []
    for i in range(6):
        segments.append((syllable_voiced, "voiced"))
        if i == 2:
            segments.append((pause, "pause"))
        elif i < 5:
            segments.append((syllable_gap, "gap"))

    pieces: List[np.ndarray] = []
    for duration, kind in segments:
        n = int(duration * sample_rate)
        t = np.arange(n) / sample_rate
        if kind == "voiced":
            wave = 0.6 * np.sin(2 * np.pi * 700.0 * t) + 0.4 * np.sin(2 * np.pi * 1200.0 * t)
            envelope = np.sin(np.linspace(0.0, np.pi, n)) ** 0.5  # 音节自然起收
            pieces.append(wave * envelope)
        else:
            pieces.append(np.zeros(n))
    audio = np.concatenate(pieces)

    # 整体 RMS 校准（只对浊音段归一，静音段保持 0）
    voiced_mask = np.abs(audio) > 1e-6
    if voiced_mask.any():
        current_rms = float(np.sqrt(np.mean(audio[voiced_mask] ** 2)))
        audio[voiced_mask] *= target_rms / current_rms
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)


class RecordingRenderer(LipSyncRenderer):
    """录制全部分发的 MouthSignal（时间戳 + 张嘴值）。"""

    def __init__(self) -> None:
        self.ticks: List[tuple[float, float, float]] = []

    async def on_mouth_signal(self, signal: MouthSignal) -> None:
        self.ticks.append((asyncio.get_running_loop().time(), signal.mouth_open, signal.volume))


async def _run_scenario(analyzer_sample_rate: int) -> List[tuple[float, float, float]]:
    """按实况流式节奏喂一段合成语音，返回信号轨迹。"""
    analyzer = LipSyncAnalyzer(config={**USER_LIPSYNC_CONFIG, "sample_rate": analyzer_sample_rate})
    recorder = RecordingRenderer()
    analyzer.add_renderer(recorder)

    audio = _make_speech_like_audio(REAL_FEED_RATE)
    chunk_size = int(0.02 * REAL_FEED_RATE)  # 20ms 块，模拟 write_chunk 节奏
    analyzer.start("utt-harness")

    for i in range(0, len(audio), chunk_size):
        analyzer.feed(audio[i : i + chunk_size], REAL_FEED_RATE)
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.15)  # 播放尾部的实时余量
    analyzer.stop()
    await analyzer.wait_finished(timeout=3.0)
    return recorder.ticks


def _peak_mouth(ticks: List[tuple[float, float, float]]) -> float:
    return max((m for _, m, _ in ticks), default=0.0)


def _twitch_count(ticks: List[tuple[float, float, float]]) -> int:
    """统计 mouth 序列里 >0.15 → <0.08 → >0.15 的完整振荡次数。"""
    state = 0  # 0=未张 1=张开 2=已闭合待重开
    count = 0
    for _, mouth, _ in ticks:
        if state == 0 and mouth > 0.15:
            state = 1
        elif state == 1 and mouth < 0.08:
            state = 2
        elif state == 2 and mouth > 0.15:
            count += 1
            state = 1
    return count


class TestLipsyncAmplitudeChain:
    """口型幅度链路（用户实况配置 + 32kHz GPT-SoVITS 形态输入）。"""

    def test_normal_loudness_speech_reaches_visible_amplitude(self) -> None:
        """正常响度语音（RMS≈0.07）应产生可见口型：峰值 ≥ 0.35。"""
        ticks = asyncio.run(_run_scenario(USER_LIPSYNC_CONFIG["sample_rate"]))
        peak = _peak_mouth(ticks)
        trace = [f"{m:.3f}" for _, m, _ in ticks[-40:]]
        print(f"\n[当前配置 16k] 帧数={len(ticks)} 峰值={peak:.3f} 尾迹={trace}")
        assert peak >= 0.35, (
            f"口型幅度过小：峰值 {peak:.3f} < 0.35（max_mouth_open=0.6 的可见下限）——"
            f"复现用户症状'口型幅度很小'"
        )

    def test_no_repeated_mid_sentence_mouth_collapse(self) -> None:
        """句中不应反复闭嘴回弹（抽搐）：完整振荡 ≤ 1 次（句读停顿允许 1 次）。"""
        ticks = asyncio.run(_run_scenario(USER_LIPSYNC_CONFIG["sample_rate"]))
        twitches = _twitch_count(ticks)
        trace = [f"{m:.3f}" for _, m, _ in ticks[-40:]]
        print(f"\n[当前配置 16k] 抽搐次数={twitches} 尾迹={trace}")
        assert twitches <= 1, (
            f"句中 mouth 反复闭合回弹 {twitches} 次 > 1——复现用户症状'抽搐回初始状态'"
        )

    def test_real_sample_rate_beats_mismatched_config(self) -> None:
        """差分证据：同一音频，分析器采样率=实际喂入率(32k) 的峰值
        不应低于错配配置(16k)。修复采样率使用后此断言应保持绿。"""
        ticks_mismatch = asyncio.run(_run_scenario(16000))
        ticks_match = asyncio.run(_run_scenario(32000))
        peak_mismatch = _peak_mouth(ticks_mismatch)
        peak_match = _peak_mouth(ticks_match)
        print(f"\n[差分] 16k 错配峰值={peak_mismatch:.3f}  32k 匹配峰值={peak_match:.3f}")
        assert peak_match >= peak_mismatch - 0.02, (
            "使用真实采样率反而更差——假设不成立，需重查"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-s", "-v"])
