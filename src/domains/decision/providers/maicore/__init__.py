"""MaiCore Decision Provider"""

from src.modules.registry import ProviderRegistry

from .maicore_decision_provider import MaiCoreDecisionProvider
from .message_schema import ActionSuggestionMessage
from .router_adapter import RouterAdapter

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("maicore", MaiCoreDecisionProvider, source="builtin:maicore")

__all__ = [
    "MaiCoreDecisionProvider",
    "RouterAdapter",
    "ActionSuggestionMessage",
]
