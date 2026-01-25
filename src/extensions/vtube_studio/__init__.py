"""
VTube Studio Extension Package

将VTubeStudioPlugin包装为Extension，保持原有功能不变。
"""

from .extension import extension_class

__all__ = ["extension_class"]
