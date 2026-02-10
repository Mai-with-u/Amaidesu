"""Console Input Provider"""

from src.modules.registry import ProviderRegistry

from .console_input_provider import ConsoleInputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_input("console_input", ConsoleInputProvider, source="builtin:console_input")

__all__ = ["ConsoleInputProvider"]
