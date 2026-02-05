"""Avatar Output Provider"""
from .avatar_output_provider import AvatarOutputProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_output("avatar", AvatarOutputProvider, source="builtin:avatar")

__all__ = ["AvatarOutputProvider"]
