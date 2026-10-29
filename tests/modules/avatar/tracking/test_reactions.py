"""脉冲反应与说话韵律：脉冲起落、同时数量上限、音节与短语起点触发点头挑眉、间隔下限"""

from __future__ import annotations

import random

import pytest

from src.modules.avatar.tracking.reactions import PULSES, PulseLayer, SpeechProsody


def test_pulse_rises_holds_falls_and_disappears() -> None:
    layer = PulseLayer()
    assert layer.trigger("brighten", 0, scale=1.0) and not layer.trigger("不存在", 0)
    assert layer.sample(0).get("mouthSmileLeft", 0.0) == pytest.approx(0.0)
    assert layer.sample(300)["mouthSmileLeft"] == pytest.approx(0.35)  # 浮现后保持（双侧简写已展开）
    assert 0 < layer.sample(1300)["mouthSmileLeft"] < 0.35  # 回落中
    assert layer.sample(PULSES["brighten"].duration_ms) == {}  # 结束后消失


def test_pulse_scale_and_active_limit() -> None:
    layer = PulseLayer()
    layer.trigger("startle", 0, scale=0.5)
    assert layer.sample(100)["eyeWideLeft"] == pytest.approx(0.4)
    for _ in range(PulseLayer.MAX_ACTIVE + 3):
        layer.trigger("nod", 0)
    assert layer.active_count == PulseLayer.MAX_ACTIVE  # 多出的丢最早的


def _speak(prosody: SpeechProsody, pattern: list[tuple[int, float]], start: int = 0) -> list[tuple[int, str]]:
    """按 (持续毫秒, 张嘴量) 序列每 30 毫秒喂一帧，收集 (时刻, 触发的脉冲名)。"""
    fired: list[tuple[int, str]] = []
    t = start
    for duration, level in pattern:
        for _ in range(max(1, duration // 30)):
            fired += [(t, kind) for kind, _ in prosody.on_mouth(level, t)]
            t += 30
    return fired


def test_nods_land_on_onsets_with_minimum_gap_and_phrases_raise_brows() -> None:
    syllables = [(150, 0.5), (150, 0.0)] * 8  # 一个短语里连着八个音节
    pause = [(450, 0.0)]  # 短语间停顿
    prosody = SpeechProsody(random.Random(7))
    fired = _speak(prosody, (syllables + pause) * 6)
    nods = [t for t, kind in fired if kind == "nod"]
    flashes = [t for t, kind in fired if kind == "brow_flash"]
    assert nods and all(b - a >= SpeechProsody.NOD_MIN_GAP_MS for a, b in zip(nods, nods[1:], strict=False))
    assert all(b - a >= SpeechProsody.FLASH_MIN_GAP_MS for a, b in zip(flashes, flashes[1:], strict=False))
    assert len(nods) < 16  # 远少于音节数：不是每个音节都点头


class _AlwaysRng(random.Random):
    """每次抽签都"中"：让概率触发变成确定触发，便于断言起点判定本身。"""

    def random(self) -> float:
        return 0.0


def test_onset_needs_a_real_gap_and_end_of_sentence_resets_phrase_start() -> None:
    prosody = SpeechProsody(_AlwaysRng())
    assert [k for k, _ in prosody.on_mouth(0.6, 0)] == ["nod", "brow_flash"]  # 一句话的第一个起点算短语开头
    assert prosody.on_mouth(0.0, 30) == [] and prosody.on_mouth(0.6, 60) == []  # 只停了 30 毫秒：不算新音节
    assert _speak(prosody, [(2000, 0.6)], start=90) == []  # 一直张着嘴没有新起点
    assert [k for k, _ in prosody.on_end(5000)] == ["nod"]  # 说完收尾点一下头
    assert [k for k, _ in prosody.on_mouth(0.6, 6000)] == ["nod", "brow_flash"]  # 下一句重新算短语开头
