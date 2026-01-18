"""
Sticker Output Provider
"""

from .sticker_output_provider import StickerOutputProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_output("sticker", StickerOutputProvider, source="builtin:sticker")

__all__ = ["StickerOutputProvider"]
