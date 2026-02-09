"""MaiCore Decision Provider"""

from .maicore_decision_provider import MaiCoreDecisionProvider
from .router_adapter import RouterAdapter
from .message_schema import ActionSuggestionMessage
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("maicore", MaiCoreDecisionProvider, source="builtin:maicore")

__all__ = [
    "MaiCoreDecisionProvider",
    "RouterAdapter",
    "ActionSuggestionMessage",
]
