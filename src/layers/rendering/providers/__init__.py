"""
内置 Output Provider 模块

此模块包含所有内置的 OutputProvider，它们会在模块加载时自动注册到 ProviderRegistry。
"""

from src.layers.rendering.provider_registry import ProviderRegistry

# 导入所有内置 OutputProvider
from .avatar_output_provider import AvatarOutputProvider
from .tts_provider import TTSProvider
from .subtitle_provider import SubtitleProvider
from .sticker_provider import StickerProvider
from .vts_provider import VTSProvider
from .omni_tts_provider import OmniTTSProvider

# 注册所有内置 OutputProvider 到 Registry
# 内置 Provider 使用 "builtin:<name>" 作为来源标识

ProviderRegistry.register_output("avatar", AvatarOutputProvider, source="builtin:avatar")
ProviderRegistry.register_output("tts", TTSProvider, source="builtin:tts")
ProviderRegistry.register_output("subtitle", SubtitleProvider, source="builtin:subtitle")
ProviderRegistry.register_output("sticker", StickerProvider, source="builtin:sticker")
ProviderRegistry.register_output("vts", VTSProvider, source="builtin:vts")
ProviderRegistry.register_output("omni_tts", OmniTTSProvider, source="builtin:omni_tts")

# 导出列表
__all__ = [
    "AvatarOutputProvider",
    "TTSProvider",
    "SubtitleProvider",
    "StickerProvider",
    "VTSProvider",
    "OmniTTSProvider",
]

