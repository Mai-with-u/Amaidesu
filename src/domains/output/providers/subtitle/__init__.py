"""Subtitle Output Provider"""

from src.modules.registry import ProviderRegistry

from .subtitle_provider import SubtitleOutputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_output("subtitle", SubtitleOutputProvider, source="builtin:subtitle")

__all__ = ["SubtitleOutputProvider"]
