"""GPT-SoVITS Output Provider"""
from .gptsovits_provider import GPTSoVITSOutputProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_output("gptsovits", GPTSoVITSOutputProvider, source="builtin:gptsovits")

__all__ = ["GPTSoVITSOutputProvider"]
