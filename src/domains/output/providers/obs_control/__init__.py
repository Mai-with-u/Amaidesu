"""OBS Control Output Provider"""

from src.modules.registry import ProviderRegistry

from .obs_control_provider import ObsControlOutputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_output("obs_control", ObsControlOutputProvider, source="builtin:obs_control")

__all__ = ["ObsControlOutputProvider"]
