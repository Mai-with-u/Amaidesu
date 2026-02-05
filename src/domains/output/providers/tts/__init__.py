"""TTS Provider"""
from .tts_provider import TTSProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_output("tts", TTSProvider, source="builtin:tts")

__all__ = ["TTSProvider"]
