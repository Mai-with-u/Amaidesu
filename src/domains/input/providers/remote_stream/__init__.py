"""Remote Stream Provider (WARNING: 实际上是 OutputProvider，不应放在 input/providers)"""
from .remote_stream_provider import RemoteStreamProvider

# 注意：RemoteStreamProvider 是 OutputProvider，不是 InputProvider
# 应该移到 src/layers/rendering/providers/remote_stream/
# 临时放在这里，需要重构

__all__ = ["RemoteStreamProvider"]
