"""Emotion 全局枚举。

所有阶段共享的情绪词汇表(12 个值)。
放在 `src.modules.types` 供采集器/Agent/工具各层直接引用，避免跨层依赖。
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
