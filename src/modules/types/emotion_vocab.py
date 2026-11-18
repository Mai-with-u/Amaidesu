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


# 模型偶发输出中文情绪名（实测泄漏例："害羞：我错了…"整行连前缀进了 TTS）。
# 这里给每个枚举值配常用的中文叫法，输出解析侧统一用它认中文前缀与中文枚举值；
# 不在表里的中文词不认——普通中文台词（如"大家：…"）不会被误当情绪标签剥掉。
EMOTION_CN_ALIASES: dict[str, "Emotion"] = {
    "平静": Emotion.NEUTRAL,
    "淡定": Emotion.NEUTRAL,
    "开心": Emotion.HAPPY,
    "高兴": Emotion.HAPPY,
    "快乐": Emotion.HAPPY,
    "难过": Emotion.SAD,
    "伤心": Emotion.SAD,
    "生气": Emotion.ANGRY,
    "愤怒": Emotion.ANGRY,
    "惊讶": Emotion.SURPRISED,
    "吃惊": Emotion.SURPRISED,
    "害怕": Emotion.SCARED,
    "恐惧": Emotion.SCARED,
    "恶心": Emotion.DISGUSTED,
    "讨厌": Emotion.DISGUSTED,
    "害羞": Emotion.SHY,
    "不好意思": Emotion.EMBARRASSED,
    "尴尬": Emotion.EMBARRASSED,
    "困惑": Emotion.CONFUSED,
    "疑惑": Emotion.CONFUSED,
    "心动": Emotion.LOVE,
    "喜爱": Emotion.LOVE,
    "兴奋": Emotion.EXCITED,
    "激动": Emotion.EXCITED,
    "得意": Emotion.SMUG,
    "嘚瑟": Emotion.SMUG,
    "认真": Emotion.SERIOUS,
    "严肃": Emotion.SERIOUS,
    "累": Emotion.TIRED,
    "疲惫": Emotion.TIRED,
    "哭": Emotion.CRYING,
    "想哭": Emotion.CRYING,
    "无语": Emotion.SPEECHLESS,
}


__all__ = ["Emotion", "EMOTION_CN_ALIASES"]
