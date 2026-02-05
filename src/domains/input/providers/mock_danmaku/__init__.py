"""Mock Danmaku Input Provider"""
from .mock_danmaku_provider import MockDanmakuInputProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_input("mock_danmaku", MockDanmakuInputProvider, source="builtin:mock_danmaku")

__all__ = ["MockDanmakuInputProvider"]
