"""
输出Provider配置Schema

定义所有输出Provider的Pydantic配置模型。

注意：以下Provider已迁移到自管理Schema架构：
- SubtitleProvider: src/domains/output/providers/subtitle/subtitle_provider.py
- VTSProvider: src/domains/output/providers/vts/vts_provider.py
- TTSProvider: src/domains/output/providers/tts/tts_provider.py
- StickerProvider: src/domains/output/providers/sticker/sticker_output_provider.py
- WarudoProvider: src/domains/output/providers/warudo/warudo_provider.py
- ObsControlProvider: src/domains/output/providers/obs_control/obs_control_provider.py
- GPTSoVITSProvider: src/domains/output/providers/gptsovits/gptsovits_provider.py
- OmniTTSProvider: src/domains/output/providers/omni_tts/omni_tts_provider.py
- AvatarProvider: src/domains/output/providers/avatar/avatar_output_provider.py
- RemoteStreamProvider: src/domains/output/providers/remote_stream/remote_stream_output_provider.py
"""

from typing import Dict, Any
from pydantic import BaseModel

from .base import BaseProviderConfig


# 所有Output Provider已迁移到自管理Schema架构
# 集中式Schema已弃用，请使用ProviderRegistry.get_config_schema()获取Provider的Schema


# 空的映射，用于向后兼容
OUTPUT_PROVIDER_CONFIG_MAP: Dict[str, BaseProviderConfig] = {}


def get_output_provider_config(provider_type: str, config: Dict[str, Any]) -> BaseModel:
    """
    获取输出Provider配置对象

    注意：此函数已弃用，请使用ProviderRegistry.get_config_schema()

    Args:
        provider_type: Provider类型
        config: 配置字典

    Returns:
        对应的配置对象

    Raises:
        ValueError: 如果provider_type不支持
    """
    from src.core.provider_registry import ProviderRegistry

    schema_class = ProviderRegistry.get_config_schema(provider_type)
    if not schema_class:
        raise ValueError(f"不支持的输出Provider类型: {provider_type}")

    return schema_class(**config)


__all__ = [
    # 工厂函数（已弃用）
    "get_output_provider_config",
    # 类型映射（空，所有Provider已迁移）
    "OUTPUT_PROVIDER_CONFIG_MAP",
]
