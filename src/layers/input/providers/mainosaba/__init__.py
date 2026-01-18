"""Mainosaba Provider - 游戏画面文本采集"""

from .mainosaba_provider import MainosabaInputProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_input("mainosaba", MainosabaInputProvider, source="builtin:mainosaba")

__all__ = ["MainosabaInputProvider"]
