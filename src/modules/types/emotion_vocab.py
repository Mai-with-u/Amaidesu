"""Emotion 全局枚举。

所有阶段共享的情绪词汇表(12 个值)。
放在 `src.modules.types` 而非 `src.stages.output` 是为了避免:
- `types.intent` -> `stages.output.emotion_vocab` -> `stages.output.__init__` -> `manager`
  -> 又间接 import `types.Intent` 的循环。
"""

from enum import Enum


class Emotion(str, Enum):
    """全局情绪枚举,小写字符串值。

    任何 handler / 任何调用方都必须使用此枚举的值,禁止自定义大小写或拼写。
    继承 `str` 是为了让序列化 / HTTP 传输时直接是字符串。
    """

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    SHY = "shy"
    LOVE = "love"
    EXCITED = "excited"
    CONFUSED = "confused"
    SCARED = "scared"
    THINKING = "thinking"
    RELAXED = "relaxed"


__all__ = ["Emotion"]
