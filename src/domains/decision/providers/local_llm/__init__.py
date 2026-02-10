"""Local LLM Decision Provider"""

from src.modules.registry import ProviderRegistry

from .local_llm_decision_provider import LocalLLMDecisionProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("local_llm", LocalLLMDecisionProvider, source="builtin:local_llm")

__all__ = ["LocalLLMDecisionProvider"]
