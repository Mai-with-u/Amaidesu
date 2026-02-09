"""KeywordActionDecisionProvider - 关键词动作决策Provider

基于规则的关键词匹配决策Provider，根据配置的关键词规则生成Intent。
通过 Intent.actions 传递动作到 Output Domain，不直接触发 Output Provider。
"""

from .keyword_action_decision_provider import KeywordActionDecisionProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_decision("keyword_action", KeywordActionDecisionProvider, source="builtin:keyword_action")

__all__ = ["KeywordActionDecisionProvider"]
