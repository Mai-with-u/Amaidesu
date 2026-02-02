"""Rule Engine Decision Provider"""
from .rule_engine_decision_provider import RuleEngineDecisionProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("rule_engine", RuleEngineDecisionProvider, source="builtin:rule_engine")

__all__ = ["RuleEngineDecisionProvider"]
