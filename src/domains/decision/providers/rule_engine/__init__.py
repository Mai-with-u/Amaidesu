"""Rule Engine Decision Provider"""

from src.modules.registry import ProviderRegistry

from .rule_engine_decision_provider import RuleEngineDecisionProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("rule_engine", RuleEngineDecisionProvider, source="builtin:rule_engine")

__all__ = ["RuleEngineDecisionProvider"]
