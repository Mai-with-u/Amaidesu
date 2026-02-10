"""ReadPingmu Input Provider"""

from src.modules.registry import ProviderRegistry

from .read_pingmu_provider import ReadPingmuInputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_input("read_pingmu", ReadPingmuInputProvider, source="builtin:read_pingmu")

__all__ = ["ReadPingmuInputProvider"]
