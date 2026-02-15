"""Pydantic schemas for configuration validation."""

from typing import Dict, Type

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

# Provider Schema Registry
PROVIDER_SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {}


# Helper functions
def get_provider_schema(provider_type: str, provider_layer: str = None) -> Type[BaseModel]:
    """
    获取Provider的Schema类

    从 ProviderRegistry 获取。

    Args:
        provider_type: Provider类型标识
        provider_layer: Provider层级（可选）

    Returns:
        对应的Schema类

    Raises:
        KeyError: 如果provider_type未注册
    """
    from src.modules.registry import ProviderRegistry

    schema = ProviderRegistry.get_config_schema(provider_type)
    if schema is not None:
        return schema

    # 如果找不到Schema，尝试导入 providers 包以触发注册
    # 这发生在 get_provider_config_with_defaults 被调用时，
    # providers 包的 __init__.py 可能还没有被执行
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
    schema = ProviderRegistry.get_config_schema(provider_type)
    if schema is not None:
        return schema

    raise KeyError(f"未注册的Provider类型: {provider_type} (layer: {provider_layer})")


def validate_provider_config(provider_type: str, config: dict) -> BaseModel:
    """
    验证Provider配置

    Args:
        provider_type: Provider类型
        config: 配置字典

    Returns:
        验证后的配置对象

    Raises:
        KeyError: 如果provider_type未注册
        ValidationError: 如果配置验证失败
    """
    schema_class = get_provider_schema(provider_type)
    return schema_class(**config)


def list_all_providers() -> dict:
    """
    列出所有已注册的Provider

    从ProviderRegistry获取所有已注册的Provider（自管理Schema架构）。

    Returns:
        包含分类列表的字典:
        {
            "input": [...],
            "decision": [...],
            "output": [...],
            "total": N
        }
    """
    from src.modules.registry import ProviderRegistry

    input_providers = ProviderRegistry.get_registered_input_providers()
    decision_providers = ProviderRegistry.get_registered_decision_providers()
    output_providers = ProviderRegistry.get_registered_output_providers()

    return {
        "input": sorted(input_providers),
        "decision": sorted(decision_providers),
        "output": sorted(output_providers),
        "total": len(input_providers) + len(decision_providers) + len(output_providers),
    }


def verify_no_enabled_field_in_schemas() -> list:
    """
    验证所有Provider Schema中都不包含'enabled'字段

    从ProviderRegistry检查所有已注册Provider的Schema。

    Returns:
        包含'enabled'字段的Schema列表（应为空）

    Note:
        这是架构约束测试。Provider的enabled状态由Manager统一管理，
        Schema中不应包含enabled字段。
    """
    from src.modules.registry import ProviderRegistry

    schemas_with_enabled = []

    # 检查所有域的Provider
    all_providers = ProviderRegistry.get_all_providers()

    for domain, provider_names in all_providers.items():
        for provider_name in provider_names:
            schema_class = ProviderRegistry.get_config_schema(provider_name)
            if schema_class and "enabled" in schema_class.model_fields:
                schemas_with_enabled.append(f"{domain}:{provider_name}")

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
]
