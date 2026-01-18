"""MaiCore Decision Provider"""
from .maicore_decision_provider import MaiCoreDecisionProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("maicore", MaiCoreDecisionProvider, source="builtin:maicore")

__all__ = ["MaiCoreDecisionProvider"]
