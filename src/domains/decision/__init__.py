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

__all__ = [
    "DecisionProviderManager",
]
