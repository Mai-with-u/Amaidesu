"""Subtitle Output Provider"""
from .subtitle_provider import SubtitleOutputProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_output("subtitle", SubtitleOutputProvider, source="builtin:subtitle")

__all__ = ["SubtitleOutputProvider"]
