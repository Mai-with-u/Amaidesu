"""Remote Stream Provider"""
from .remote_stream_provider import RemoteStreamProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_input("remote_stream", RemoteStreamProvider, source="builtin:remote_stream")

__all__ = ["RemoteStreamProvider"]
