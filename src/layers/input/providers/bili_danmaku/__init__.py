"""
Bilibili 弹幕输入Provider
"""

from .bili_danmaku_provider import BiliDanmakuInputProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_input("bili_danmaku", BiliDanmakuInputProvider, source="builtin:bili_danmaku")

__all__ = ["BiliDanmakuInputProvider"]
