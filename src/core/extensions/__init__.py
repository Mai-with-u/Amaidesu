"""
扩展系统模块

导出Extension接口、ExtensionInfo和ExtensionManager
"""

from ..extension import Extension, ExtensionInfo, BaseExtension
from ..extension_manager import (
    ExtensionDependencyError,
    ExtensionLoadError,
    ExtensionManager,
)

__all__ = [
    # Extension接口和基类
    "Extension",
    "ExtensionInfo",
    "BaseExtension",
    # ExtensionManager和异常
    "ExtensionManager",
    "ExtensionLoadError",
    "ExtensionDependencyError",
]
