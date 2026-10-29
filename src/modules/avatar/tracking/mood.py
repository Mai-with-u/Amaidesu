"""心情：比表情慢得多的身体底色

表情是一句话的事，心情是一阵子的事。心情用两个维度表示：愉快度（不快 -1 ~ 愉快 +1）
和兴奋度（平静 -1 ~ 兴奋 +1）。每个表情上脸时按情绪推它一下（越强推得越多），礼物、
受惊这类脉冲也轻推一下；没有新刺激时按半衰期慢慢回到人设基线（0, 0）。

心情反过来给身体一个底色，作用在所有表情之下：
- 愉快度 → 静止时嘴角高一点或低一点、眉略抬或略压；
- 兴奋度 → 兴奋时眼睛略睁大、头部微动更多、眨眼更勤；平静或疲惫时眼皮略沉、动作变少。
这样连着几句开心的话之后，句间的静止脸也带着笑；打了一整局累了，形象会慢下来。
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from .channels import BROW, EYE_OPEN, EYE_WIDE, MOUTH_SMILE

# 情绪 → 对心情的推动（愉快度, 兴奋度），按满强度计
MOOD_PUSH: Dict[str, Tuple[float, float]] = {
    "happy": (0.5, 0.2),
    "excited": (0.5, 0.6),
    "love": (0.6, 0.1),
    "sad": (-0.5, -0.3),
    "crying": (-0.7, 0.1),
    "angry": (-0.5, 0.5),
    "disgusted": (-0.4, 0.1),
    "surprised": (0.0, 0.5),
    "scared": (-0.4, 0.6),
    "confused": (-0.1, 0.1),
    "serious": (-0.1, 0.0),
    "shy": (0.2, 0.1),
    "embarrassed": (-0.1, 0.2),
    "smug": (0.3, 0.0),
    "speechless": (-0.2, -0.2),
    "tired": (-0.1, -0.7),
}

# 脉冲反应对心情的轻推（愉快度, 兴奋度）：收到礼物高兴一点，吓一跳兴奋一点
PULSE_PUSH: Dict[str, Tuple[float, float]] = {
    "brighten": (0.08, 0.05),
    "startle": (0.0, 0.15),
    "alert": (0.0, 0.08),
    "wince": (-0.08, 0.05),
}


def _clamp(value: float) -> float:
    return min(1.0, max(-1.0, value))


class Mood:
    """心情状态（愉快度、兴奋度）与它给身体的底色。"""

    def __init__(self, *, half_life_ms: int, gain: float) -> None:
        self._half_life_ms = max(1, half_life_ms)
        self._gain = gain
        self.valence = 0.0
        self.arousal = 0.0
        self._at_ms: Optional[int] = None

    def decay_to(self, now_ms: int) -> None:
        """按半衰期把心情往基线回收到 ``now_ms`` 时刻。"""
        if self._at_ms is not None and now_ms > self._at_ms:
            factor = 0.5 ** ((now_ms - self._at_ms) / self._half_life_ms)
            self.valence *= factor
            self.arousal *= factor
        self._at_ms = now_ms if self._at_ms is None else max(self._at_ms, now_ms)

    def nudge(self, valence: float, arousal: float, now_ms: int) -> None:
        """直接推一下心情（已乘好幅度）。"""
        self.decay_to(now_ms)
        self.valence = _clamp(self.valence + valence)
        self.arousal = _clamp(self.arousal + arousal)

    def push_emotion(self, emotion: str, level: float, now_ms: int) -> None:
        """一个表情上脸：按情绪表推心情，幅度 = 推动量 × 表情实际幅度 × 增益。"""
        push = MOOD_PUSH.get(emotion)
        if push is not None:
            self.nudge(push[0] * level * self._gain, push[1] * level * self._gain, now_ms)

    def push_pulse(self, kind: str, scale: float, now_ms: int) -> None:
        """一个脉冲反应：按脉冲表轻推心情。"""
        push = PULSE_PUSH.get(kind)
        if push is not None:
            self.nudge(push[0] * scale, push[1] * scale, now_ms)

    def offsets(self) -> Dict[str, float]:
        """心情给身体的底色（叠在表情之下的通道偏移）。"""
        arousal = self.arousal
        return {
            MOUTH_SMILE: 0.15 * self.valence,
            BROW: 0.06 * self.valence,
            EYE_WIDE: 0.15 * max(arousal, 0.0),
            EYE_OPEN: 0.12 * min(arousal, 0.0),
        }

    def motion_scale(self) -> float:
        """头部微动幅度倍数：兴奋时多动，平静疲惫时少动。"""
        return 1.0 + 0.35 * self.arousal

    def blink_scale(self) -> float:
        """眨眼频率倍数：兴奋时眨得勤一点。"""
        return 1.0 + 0.25 * self.arousal


__all__ = ["MOOD_PUSH", "PULSE_PUSH", "Mood"]
