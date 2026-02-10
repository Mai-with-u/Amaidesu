"""Mainosaba Provider - 游戏画面文本采集"""

from src.modules.registry import ProviderRegistry

from .mainosaba_provider import MainosabaInputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_input("mainosaba", MainosabaInputProvider, source="builtin:mainosaba")

__all__ = ["MainosabaInputProvider"]
