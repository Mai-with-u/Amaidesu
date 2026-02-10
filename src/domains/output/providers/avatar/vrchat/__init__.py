from src.modules.registry import ProviderRegistry
from .vrchat_provider import VRChatProvider

ProviderRegistry.register_output("vrchat", VRChatProvider, source="builtin:vrchat")

__all__ = ["VRChatProvider"]
