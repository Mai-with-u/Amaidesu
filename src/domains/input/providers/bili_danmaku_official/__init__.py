"""
Bilibili 官方弹幕输入Provider
"""

from src.modules.registry import ProviderRegistry

from .bili_official_provider import BiliDanmakuOfficialInputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_input(
    "bili_danmaku_official", BiliDanmakuOfficialInputProvider, source="builtin:bili_danmaku_official"
)

__all__ = ["BiliDanmakuOfficialInputProvider"]
