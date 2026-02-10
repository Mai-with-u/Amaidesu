"""
Decision Layer

决策层，负责接收NormalizedMessage并返回Intent。
"""

# 从 core.types 导入共享类型
from src.modules.types import (
    ActionType,
    EmotionType,
    Intent,
    IntentAction,
    SourceContext,
)

from .coordinator import DecisionCoordinator
from .provider_manager import DecisionProviderManager

# 向后兼容：DecisionManager 作为 DecisionProviderManager 的别名
DecisionManager = DecisionProviderManager

__all__ = [
    "Intent",
    "EmotionType",
    "ActionType",
    "IntentAction",
    "SourceContext",
    "DecisionProviderManager",
    "DecisionCoordinator",
    "DecisionManager",  # 向后兼容
]
