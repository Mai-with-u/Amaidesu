"""Remote Stream Output Provider"""

# 注意：实际的 Provider 实现在 src/layers/input/providers/remote_stream/
# 这是一个临时别名，为了保持 Provider 在正确的位置注册

from src.layers.input.providers.remote_stream.remote_stream_provider import RemoteStreamProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry (作为 OutputProvider)
ProviderRegistry.register_output("remote_stream", RemoteStreamProvider, source="builtin:remote_stream")

__all__ = ["RemoteStreamProvider"]
