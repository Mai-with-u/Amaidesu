"""
Decision Layer

决策层，负责接收NormalizedMessage并返回Intent。
"""

from .intent import (
    Intent,
    EmotionType,
    ActionType,
    IntentAction,
    SourceContext,
    ActionSuggestion,
)
from .provider_manager import DecisionProviderManager
from .coordinator import DecisionCoordinator

# 向后兼容：DecisionManager 作为 DecisionProviderManager 的别名
DecisionManager = DecisionProviderManager

__all__ = [
    "Intent",
    "EmotionType",
    "ActionType",
    "IntentAction",
    "SourceContext",
    "ActionSuggestion",
    "DecisionProviderManager",
    "DecisionCoordinator",
    "DecisionManager",  # 向后兼容
]
