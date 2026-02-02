"""Omni TTS Provider"""
from .omni_tts_provider import OmniTTSProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_output("omni_tts", OmniTTSProvider, source="builtin:omni_tts")

__all__ = ["OmniTTSProvider"]
