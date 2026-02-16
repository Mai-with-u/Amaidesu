"""LLM Decision Provider"""

from src.modules.registry import ProviderRegistry

from .llm_decision_provider import LLMDecisionProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("llm", LLMDecisionProvider, source="builtin:llm")

__all__ = ["LLMDecisionProvider"]
