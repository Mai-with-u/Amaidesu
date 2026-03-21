"""核心类型定义

跨域共享的类型定义，避免循环依赖。

类型说明：
- Intent: 决策意图，使用自然语言 emotion/action/speech
- IntentMetadata: 意图元数据
- EmotionType: 情感类型（字符串别名）
- ActionType: 动作类型（字符串别名）
"""

from .intent import (
    ActionType,
    EmotionType,
    Intent,
    IntentMetadata,
)

__all__ = [
    "ActionType",
    "EmotionType",
    "Intent",
    "IntentMetadata",
]
