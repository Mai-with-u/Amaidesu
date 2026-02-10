"""Warudo Avatar Provider"""

from src.modules.registry import ProviderRegistry

from .warudo_provider import WarudoOutputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_output("warudo", WarudoOutputProvider, source="builtin:warudo")

__all__ = ["WarudoOutputProvider"]
