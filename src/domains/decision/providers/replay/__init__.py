"""
ReplayDecisionProvider - 输入重放决策提供者
"""

from src.modules.registry import ProviderRegistry
from .replay_decision_provider import ReplayDecisionProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_decision(
    name="replay",
    provider_class=ReplayDecisionProvider,
    source="builtin:replay",
)

__all__ = [
    "ReplayDecisionProvider",
]
