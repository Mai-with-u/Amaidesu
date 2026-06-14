"""Pydantic schemas for configuration validation."""

from typing import Dict, Optional, Type

from pydantic import BaseModel

# Base schema
from .base import BaseProviderConfig

# Non-provider schemas (system-wide configurations)
from .logging import LoggingConfig

# Output provider schemas
from .output_providers import (
    OUTPUT_PROVIDER_CONFIG_MAP,
    get_output_provider_config,
)

from src.modules.logging import get_logger

logger = get_logger(__name__)

# Provider Schema Registry - 内存存储
PROVIDER_SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {}


def register_provider_schema(provider_type: str, schema_class: Type[BaseModel]) -> None:
    """
    注册 Provider 的配置 Schema

    Args:
        provider_type: Provider 类型标识
        schema_class: Schema 类
    """
    PROVIDER_SCHEMA_REGISTRY[provider_type] = schema_class


def get_provider_schema(provider_type: str, provider_layer: Optional[str] = None) -> Type[BaseModel]:
    """
    获取 Provider 的 Schema 类

    Args:
        provider_type: Provider 类型标识
        provider_layer: Provider 层级（可选）

    Returns:
        对应的 Schema 类

    Raises:
        KeyError: 如果 provider_type 未注册
    """
    schema = PROVIDER_SCHEMA_REGISTRY.get(provider_type)
    if schema is not None:
        return schema

    # 如果找不到 Schema，尝试触发 providers 包的导入以进行自我注册
    try:
        if provider_layer == "input":
            from src.domains.input import providers as _  # noqa: F401
        elif provider_layer == "output":
            from src.domains.output import providers as _  # noqa: F401
        elif provider_layer == "decision":
            from src.domains.decision import providers as _  # noqa: F401
    except ImportError as e:
        logger.debug(f"导入 providers 包失败: {e}")

    # 重新尝试获取
    schema = PROVIDER_SCHEMA_REGISTRY.get(provider_type)
    if schema is not None:
        return schema

    raise KeyError(f"未注册的 Provider 类型: {provider_type} (layer: {provider_layer})")


def validate_provider_config(provider_type: str, config: dict) -> BaseModel:
    """
    验证 Provider 配置

    Args:
        provider_type: Provider 类型
        config: 配置字典

    Returns:
        验证后的配置对象

    Raises:
        KeyError: 如果 provider_type 未注册
        ValidationError: 如果配置验证失败
    """
    schema_class = get_provider_schema(provider_type)
    return schema_class(**config)


def list_all_providers() -> dict:
    """
    列出所有已注册的 Provider

    Returns:
        包含分类列表的字典:
        {
            "input": [...],
            "decision": [...],
            "output": [...],
            "total": N
        }
    """
    return {
        "input": [],
        "decision": [],
        "output": sorted(PROVIDER_SCHEMA_REGISTRY.keys()),
        "total": len(PROVIDER_SCHEMA_REGISTRY),
    }


def verify_no_enabled_field_in_schemas() -> list:
    """
    验证所有 Provider Schema 中都不包含 'enabled' 字段

    Returns:
        包含 'enabled' 字段的 Schema 列表（应为空）
    """
    schemas_with_enabled = []

    for provider_name, schema_class in PROVIDER_SCHEMA_REGISTRY.items():
        if hasattr(schema_class, "model_fields") and "enabled" in schema_class.model_fields:
            schemas_with_enabled.append(provider_name)

    return schemas_with_enabled


__all__ = [
    # Base schemas
    "BaseProviderConfig",
    # Output provider schemas
    "OUTPUT_PROVIDER_CONFIG_MAP",
    "get_output_provider_config",
    # Non-provider schemas
    "LoggingConfig",
    # Registry
    "PROVIDER_SCHEMA_REGISTRY",
    # Helper functions
    "get_provider_schema",
    "validate_provider_config",
    "list_all_providers",
    "verify_no_enabled_field_in_schemas",
    "register_provider_schema",
]
