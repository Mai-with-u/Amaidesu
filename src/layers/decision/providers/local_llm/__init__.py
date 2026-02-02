"""Local LLM Decision Provider"""
from .local_llm_decision_provider import LocalLLMDecisionProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("local_llm", LocalLLMDecisionProvider, source="builtin:local_llm")

__all__ = ["LocalLLMDecisionProvider"]
