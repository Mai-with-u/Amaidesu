"""Emotion 全局枚举。

所有阶段共享的情绪词汇表(17 个值,三组:基础 7 / 次级 5 / 主播向 5)。
放在 `src.modules.types` 供采集器/Agent/工具各层直接引用，避免跨层依赖。
"""

from enum import Enum


class Emotion(str, Enum):
    """全局情绪枚举,小写字符串值。

    任何 handler / 任何调用方都必须使用此枚举的值,禁止自定义大小写或拼写。
    继承 `str` 是为了让序列化 / HTTP 传输时直接是字符串。
    """

    # 基础 7（Ekman 6 + neutral）
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    SCARED = "scared"
    DISGUSTED = "disgusted"

    # 次级 5（自我意识与唤起家族）
    SHY = "shy"
    EMBARRASSED = "embarrassed"
    CONFUSED = "confused"
    LOVE = "love"
    EXCITED = "excited"

    # 主播向 5（直播行业高频）
    SMUG = "smug"
    SERIOUS = "serious"
    TIRED = "tired"
    CRYING = "crying"
    SPEECHLESS = "speechless"


__all__ = ["Emotion"]
