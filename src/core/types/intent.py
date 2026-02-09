"""
跨域共享的 Intent 相关类型

这些类型被 Input/Decision/Output Domain 共享，
因此放在 src/core/types/ 中避免循环依赖。
"""

from enum import Enum
from typing import Dict, Any
from pydantic import BaseModel, Field


class EmotionType(str, Enum):
    """情感类型枚举"""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    LOVE = "love"
    SHY = "shy"
    EXCITED = "excited"
    CONFUSED = "confused"
    SCARED = "scared"

    @classmethod
    def get_default(cls) -> "EmotionType":
        """获取默认情感类型"""
        return cls.NEUTRAL


class ActionType(str, Enum):
    """动作类型枚举"""

    EXPRESSION = "expression"  # 表情
    HOTKEY = "hotkey"  # 热键
    EMOJI = "emoji"  # emoji表情
    BLINK = "blink"  # 眨眼
    NOD = "nod"  # 点头
    SHAKE = "shake"  # 摇头
    WAVE = "wave"  # 挥手
    CLAP = "clap"  # 鼓掌
    NONE = "none"  # 无动作

    @classmethod
    def get_default(cls) -> "ActionType":
        """获取默认动作类型"""
        return cls.EXPRESSION


class IntentAction(BaseModel):
    """意图动作"""

    type: ActionType = Field(..., description="动作类型")
    params: Dict[str, Any] = Field(default_factory=dict, description="动作参数")
    priority: int = Field(default=50, ge=0, le=100, description="优先级（越高越优先）")

    class Config:
        use_enum_values = True


__all__ = ["EmotionType", "ActionType", "IntentAction"]
