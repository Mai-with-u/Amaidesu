"""核心类型定义

跨域共享的类型定义，避免循环依赖。
"""

from .intent import ActionType, EmotionType, IntentAction

__all__ = ["EmotionType", "ActionType", "IntentAction"]
