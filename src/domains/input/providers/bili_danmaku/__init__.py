"""
Bilibili 弹幕输入Provider
"""

from src.modules.registry import ProviderRegistry

from .bili_danmaku_provider import BiliDanmakuInputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_input("bili_danmaku", BiliDanmakuInputProvider, source="builtin:bili_danmaku")

__all__ = ["BiliDanmakuInputProvider"]
