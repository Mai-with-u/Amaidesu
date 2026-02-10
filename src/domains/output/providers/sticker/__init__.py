"""
Sticker Output Provider
"""

from src.modules.registry import ProviderRegistry

from .sticker_output_provider import StickerOutputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_output("sticker", StickerOutputProvider, source="builtin:sticker")

__all__ = ["StickerOutputProvider"]
