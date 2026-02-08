"""Warudo Output Provider"""

from .warudo_provider import WarudoOutputProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_output("warudo", WarudoOutputProvider, source="builtin:warudo")

__all__ = ["WarudoOutputProvider"]
