"""VTS Provider"""
from .vts_provider import VTSProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_output("vts", VTSProvider, source="builtin:vts")

__all__ = ["VTSProvider"]
