"""
Bilibili 官方弹幕输入Provider
"""

from .bili_official_provider import BiliDanmakuOfficialInputProvider
from src.core.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_input(
    "bili_danmaku_official", BiliDanmakuOfficialInputProvider, source="builtin:bili_danmaku_official"
)

__all__ = ["BiliDanmakuOfficialInputProvider"]
