"""ReadPingmu Input Provider"""
from .read_pingmu_provider import ReadPingmuInputProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_input("read_pingmu", ReadPingmuInputProvider, source="builtin:read_pingmu")

__all__ = ["ReadPingmuInputProvider"]
