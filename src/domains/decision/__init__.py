"""
Decision Layer

决策层，负责接收NormalizedMessage并返回Intent。
"""

# 从 core.types 导入共享类型
from src.core.types import EmotionType, ActionType, IntentAction

from .intent import (
    Intent,
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
