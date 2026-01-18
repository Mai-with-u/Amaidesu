# providers/__init__.py

from .bili_official_maicraft_provider import BiliDanmakuOfficialMaiCraftInputProvider
from src.layers.rendering.provider_registry import ProviderRegistry

# 注册到 ProviderRegistry
ProviderRegistry.register_input("bili_danmaku_official_maicraft", BiliDanmakuOfficialMaiCraftInputProvider, source="builtin:bili_danmaku_official_maicraft")

__all__ = ["BiliDanmakuOfficialMaiCraftInputProvider"]
