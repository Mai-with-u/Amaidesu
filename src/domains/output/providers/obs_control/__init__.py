"""OBS Control Output Provider"""

from .obs_control_provider import ObsControlOutputProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_output("obs_control", ObsControlOutputProvider, source="builtin:obs_control")

__all__ = ["ObsControlOutputProvider"]
