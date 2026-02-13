"""
Decision Layer

决策层，负责接收NormalizedMessage并返回Intent。

重构说明（2026-02-13）：
- DecisionCoordinator 已合并到 DecisionProviderManager
- DecisionProviderManager 现在负责：
  - Provider 生命周期管理
  - 事件订阅/发布（data.message -> decision.intent）
  - SourceContext 构建
"""

from .provider_manager import DecisionProviderManager

# 向后兼容：DecisionManager 作为 DecisionProviderManager 的别名
DecisionManager = DecisionProviderManager

# 向后兼容：DecisionCoordinator 作为 DecisionProviderManager 的别名
DecisionCoordinator = DecisionProviderManager

__all__ = [
    "DecisionProviderManager",
    "DecisionManager",  # 向后兼容
    "DecisionCoordinator",  # 向后兼容
]
