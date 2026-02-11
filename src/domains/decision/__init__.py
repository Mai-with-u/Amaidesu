"""
Decision Layer

决策层，负责接收NormalizedMessage并返回Intent。
"""

from .coordinator import DecisionCoordinator
from .provider_manager import DecisionProviderManager

# 向后兼容：DecisionManager 作为 DecisionProviderManager 的别名
DecisionManager = DecisionProviderManager

__all__ = [
    "DecisionProviderManager",
    "DecisionCoordinator",
    "DecisionManager",  # 向后兼容
]
