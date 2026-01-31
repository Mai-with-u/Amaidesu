"""
Understanding模块 - Layer 4: 表现理解层

职责:
- 定义Intent数据结构
- 提供ResponseParser解析MessageBase为Intent
"""

from .intent import Intent, IntentAction, ActionType, EmotionType
from .response_parser import ResponseParser
from .emotion_analyzer import EmotionAnalyzer, EmotionResult

__all__ = ["Intent", "IntentAction", "ActionType", "EmotionType", "ResponseParser", "EmotionAnalyzer", "EmotionResult"]
