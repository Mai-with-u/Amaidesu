"""Mock Danmaku Input Provider"""

from src.modules.registry import ProviderRegistry

from .mock_danmaku_provider import MockDanmakuInputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_input("mock_danmaku", MockDanmakuInputProvider, source="builtin:mock_danmaku")

__all__ = ["MockDanmakuInputProvider"]
