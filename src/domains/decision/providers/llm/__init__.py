"""LLM Decision Provider"""

from src.modules.registry import ProviderRegistry

from .llm_decision_provider import LLMPDecisionProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("llm", LLMPDecisionProvider, source="builtin:llm")

__all__ = ["LLMPDecisionProvider"]
