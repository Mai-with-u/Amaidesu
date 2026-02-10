"""VTS Provider"""

from src.modules.registry import ProviderRegistry

from .vts_provider import VTSProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_output("vts", VTSProvider, source="builtin:vts")

__all__ = ["VTSProvider"]
