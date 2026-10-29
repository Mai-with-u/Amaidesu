"""脉冲反应与说话韵律：叠加在表情之上的短促动作

脉冲是几百毫秒到一两秒的小动作（点头、挑眉、眼睛一亮、受惊一缩），不进表情调度：
不会打断正在说的那句话的表情，也不受最短停留时间约束，起、持、落之后自己消失。

说话韵律从口型信号里找音节和短语的起点：嘴从闭着到张开、前面停顿够久，就是一个
起点。短语开头（停顿 0.3 秒以上）更可能轻点一下头，偶尔挑一下眉；普通音节偶尔点头。
两次点头至少隔 0.65 秒，两次挑眉至少隔 2.5 秒，避免变成机械的打拍子。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

from .behaviors import smootherstep
from .channels import BROW, EYE_OPEN, EYE_WIDE, HEAD_PITCH, HEAD_ROLL, HEAD_YAW, MOUTH_OPEN, MOUTH_SMILE


@dataclass(frozen=True)
class PulseRecipe:
    """一个脉冲：满幅时的通道偏移与起、持、落时长（毫秒）。"""

    offsets: Mapping[str, float]
    attack_ms: int
    hold_ms: int
    release_ms: int

    @property
    def duration_ms(self) -> int:
        return self.attack_ms + self.hold_ms + self.release_ms


# 头部俯仰负数 = 低头（与 VTS FaceAngleY 同号）
PULSES: Dict[str, PulseRecipe] = {
    # 说话重音处轻点一下头
    "nod": PulseRecipe({HEAD_PITCH: -2.5}, 110, 40, 260),
    # 短语开头、强调时挑一下眉
    "brow_flash": PulseRecipe({BROW: 0.35}, 100, 150, 300),
    # 收到礼物、游戏里有进展：眼睛一亮、嘴角上扬
    "brighten": PulseRecipe({MOUTH_SMILE: 0.35, BROW: 0.25, EYE_WIDE: 0.15}, 200, 700, 900),
    # 吓一跳：眼睛睁大、眉毛高抬、头往后一仰、嘴微张
    "startle": PulseRecipe({EYE_WIDE: 0.8, BROW: 0.6, HEAD_PITCH: 3.0, MOUTH_OPEN: 0.25}, 80, 250, 700),
    # "诶？"：游戏需要关注时眉毛抬起、眼睛睁大一点
    "alert": PulseRecipe({BROW: 0.4, EYE_WIDE: 0.35}, 120, 500, 700),
    # 出错了：皱眉眯眼、嘴角往下、头一低
    "wince": PulseRecipe({EYE_OPEN: -0.35, BROW: -0.4, MOUTH_SMILE: -0.3, HEAD_PITCH: -2.0}, 100, 300, 700),
    # 以下供主播主动发起的动作使用（幅度比说话时的自然点头大得多）
    "nod_deep": PulseRecipe({HEAD_PITCH: -8.0}, 140, 60, 300),
    # 摇头幅度给足：模型映射常带平滑，来回太快会被抹掉一大半（桃濑日和实测 ±8° 只剩 ±4°）
    "shake_left": PulseRecipe({HEAD_YAW: -12.0}, 140, 30, 170),
    "shake_right": PulseRecipe({HEAD_YAW: 12.0}, 140, 30, 170),
    "tilt": PulseRecipe({HEAD_ROLL: 10.0, HEAD_PITCH: -1.0}, 300, 1200, 500),
}


class PulseLayer:
    """正在进行的脉冲（最多同时 6 个，多出的丢最早的）。"""

    MAX_ACTIVE = 6

    def __init__(self) -> None:
        self._active: List[Tuple[PulseRecipe, int, float]] = []

    @property
    def active_count(self) -> int:
        """正在进行的脉冲个数（含已结束但还没被 :meth:`sample` 清掉的）。"""
        return len(self._active)

    def trigger(self, kind: str, now_ms: int, scale: float = 1.0) -> bool:
        """触发一个脉冲；未知名字返回 False。"""
        recipe = PULSES.get(kind)
        if recipe is None:
            return False
        self._active.append((recipe, now_ms, max(0.0, scale)))
        del self._active[: max(0, len(self._active) - self.MAX_ACTIVE)]
        return True

    def sample(self, now_ms: int) -> Dict[str, float]:
        """当前所有脉冲的通道偏移之和。"""
        self._active = [item for item in self._active if now_ms - item[1] < item[0].duration_ms]
        offsets: Dict[str, float] = {}
        for recipe, start, scale in self._active:
            dt = now_ms - start
            if dt < recipe.attack_ms:
                weight = smootherstep(dt / max(recipe.attack_ms, 1))
            elif dt < recipe.attack_ms + recipe.hold_ms:
                weight = 1.0
            else:
                weight = 1.0 - smootherstep((dt - recipe.attack_ms - recipe.hold_ms) / max(recipe.release_ms, 1))
            for channel, value in recipe.offsets.items():
                offsets[channel] = offsets.get(channel, 0.0) + value * weight * scale
        return offsets


class SpeechProsody:
    """从口型信号里找音节与短语起点，决定什么时候点头、什么时候挑眉。"""

    ONSET_LEVEL = 0.22
    SILENCE_LEVEL = 0.06
    SYLLABLE_GAP_MS = 120
    PHRASE_GAP_MS = 300
    NOD_MIN_GAP_MS = 650
    FLASH_MIN_GAP_MS = 2500

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._open = False
        self._quiet_since: Optional[int] = None
        self._first_onset = True
        self._last_nod = -(10**12)
        self._last_flash = -(10**12)

    def on_mouth(self, mouth_open: float, now_ms: int) -> List[Tuple[str, float]]:
        """一帧口型信号 → 这一刻要触发的脉冲（名字, 幅度）。"""
        if mouth_open < self.SILENCE_LEVEL:
            if self._open or self._quiet_since is None:
                self._quiet_since = now_ms
            self._open = False
            return []
        if self._open or mouth_open < self.ONSET_LEVEL:
            return []
        self._open = True
        gap = now_ms - self._quiet_since if self._quiet_since is not None else self.PHRASE_GAP_MS
        if gap < self.SYLLABLE_GAP_MS and not self._first_onset:
            return []
        phrase = self._first_onset or gap >= self.PHRASE_GAP_MS
        self._first_onset = False
        rng = self._rng
        pulses: List[Tuple[str, float]] = []
        if now_ms - self._last_nod >= self.NOD_MIN_GAP_MS and rng.random() < (0.65 if phrase else 0.3):
            self._last_nod = now_ms
            pulses.append(("nod", rng.uniform(0.6, 1.0) * (1.0 if phrase else 0.7)))
        if phrase and now_ms - self._last_flash >= self.FLASH_MIN_GAP_MS and rng.random() < 0.35:
            self._last_flash = now_ms
            pulses.append(("brow_flash", rng.uniform(0.7, 1.0)))
        return pulses

    def on_end(self, now_ms: int) -> List[Tuple[str, float]]:
        """一句话说完：下一句的第一个起点重新算短语开头；三成概率收尾时点一下头。"""
        self._open = False
        self._quiet_since = None
        self._first_onset = True
        if now_ms - self._last_nod >= self.NOD_MIN_GAP_MS and self._rng.random() < 0.3:
            self._last_nod = now_ms
            return [("nod", 0.7)]
        return []


__all__ = ["PULSES", "PulseLayer", "PulseRecipe", "SpeechProsody"]
