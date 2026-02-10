# providers/__init__.py

from src.modules.registry import ProviderRegistry

from .bili_official_maicraft_provider import BiliDanmakuOfficialMaiCraftInputProvider

# 注册到 ProviderRegistry
ProviderRegistry.register_input(
    "bili_danmaku_official_maicraft",
    BiliDanmakuOfficialMaiCraftInputProvider,
    source="builtin:bili_danmaku_official_maicraft",
)

__all__ = ["BiliDanmakuOfficialMaiCraftInputProvider"]
