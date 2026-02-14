"""
输出Provider配置Schema

定义所有输出Provider的Pydantic配置模型。
"""

from typing import Any, Dict

from pydantic import BaseModel

from .base import BaseProviderConfig


# Provider 配置映射
OUTPUT_PROVIDER_CONFIG_MAP: Dict[str, BaseProviderConfig] = {}


def get_output_provider_config(provider_type: str, config: Dict[str, Any]) -> BaseModel:
    """
    获取输出Provider配置对象

    Args:
        provider_type: Provider类型
        config: 配置字典

    Returns:
        对应的配置对象

    Raises:
        ValueError: 如果provider_type不支持
    """
    from src.modules.registry import ProviderRegistry

    schema_class = ProviderRegistry.get_config_schema(provider_type)
    if not schema_class:
        raise ValueError(f"不支持的输出Provider类型: {provider_type}")

    return schema_class(**config)


__all__ = [
    "get_output_provider_config",
    "OUTPUT_PROVIDER_CONFIG_MAP",
]
