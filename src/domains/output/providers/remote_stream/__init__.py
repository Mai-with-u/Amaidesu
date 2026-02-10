"""Remote Stream Output Provider"""

from src.modules.registry import ProviderRegistry

from .remote_stream_output_provider import RemoteStreamOutputProvider

# 注册到 ProviderRegistry (作为 OutputProvider)
ProviderRegistry.register_output("remote_stream", RemoteStreamOutputProvider, source="builtin:remote_stream")

__all__ = ["RemoteStreamOutputProvider"]
