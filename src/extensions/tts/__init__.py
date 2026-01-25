"""
TTS Extension Package

将TTSPlugin包装为Extension，保持原有功能不变。
"""

from .extension import extension_class

__all__ = ["extension_class"]
