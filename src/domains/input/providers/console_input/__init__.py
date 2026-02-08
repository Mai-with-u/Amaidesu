"""Console Input Provider"""

from .console_input_provider import ConsoleInputProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_input("console_input", ConsoleInputProvider, source="builtin:console_input")

__all__ = ["ConsoleInputProvider"]
