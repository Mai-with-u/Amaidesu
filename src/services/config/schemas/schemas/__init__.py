"""Pydantic schemas for configuration validation."""

from typing import Dict, Type
from pydantic import BaseModel

# Base schema
from .base import BaseProviderConfig

# Input provider schemas（已迁移到自管理 Schema，不再导入）
# from .input_providers import (
#     # ConsoleInputProviderConfig,  # 已迁移到自管理 Schema
#     # BiliDanmakuProviderConfig,  # 已迁移到自管理 Schema
#     # BiliDanmakuOfficialProviderConfig,  # 已迁移到自管理 Schema
#     # BiliDanmakuOfficialMaiCraftProviderConfig,  # 已迁移到自管理 Schema
#     # ReadPingmuProviderConfig,  # 已迁移到自管理 Schema
#     # MainosabaProviderConfig,  # 已迁移到自管理 Schema
# )

# Decision provider schemas（已迁移到自管理 Schema，不再导入）
# from .decision_providers import (
#     MaiCoreDecisionProviderConfig,  # 已迁移到自管理 Schema
#     LocalLLMDecisionProviderConfig,  # 已迁移到自管理 Schema
#     RuleEngineDecisionProviderConfig,  # 已迁移到自管理 Schema
#     MockDecisionProviderConfig,  # 已迁移到自管理 Schema
# )

# Output provider schemas（所有已迁移到自管理 Schema，只导入工厂函数）
from .output_providers import (
    OUTPUT_PROVIDER_CONFIG_MAP,
    get_output_provider_config,
)

# Non-provider schemas (system-wide configurations)
from .logging import LoggingConfig

# Provider Schema Registry
# 所有Provider已迁移到自管理Schema架构，此注册表保留为空用于向后兼容
# 实际请使用 ProviderRegistry.get_config_schema()
PROVIDER_SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {}


# Helper functions
def get_provider_schema(provider_type: str, provider_layer: str = None) -> Type[BaseModel]:
    """
    获取Provider的Schema类

    优先从 ProviderRegistry 获取（Provider 自管理 Schema），
    如果未找到则 fallback 到集中式注册表（向后兼容）。

    Args:
        provider_type: Provider类型标识
        provider_layer: Provider层级（可选，用于验证和日志）

    Returns:
        对应的Schema类

    Raises:
        KeyError: 如果provider_type未注册

    Note:
        provider_layer参数仅用于向后兼容和验证，实际Schema查找使用全局注册表。
    """
    # 优先从 ProviderRegistry 获取（Provider 自管理）
    from src.core.provider_registry import ProviderRegistry
    schema = ProviderRegistry.get_config_schema(provider_type)
    if schema is not None:
        return schema

    # fallback 到原有集中式注册表（兼容未迁移的 Provider）
    if provider_type not in PROVIDER_SCHEMA_REGISTRY:
        raise KeyError(f"未注册的Provider类型: {provider_type} (layer: {provider_layer})")
    return PROVIDER_SCHEMA_REGISTRY[provider_type]


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

    Returns:
        包含分类列表的字典:
        {
            "input": [...],
            "decision": [...],
            "output": [...],
            "total": N
        }
    """
    input_providers = [
        pt for pt in PROVIDER_SCHEMA_REGISTRY.keys()
        if any(pt == ip for ip in [
            "console_input", "bili_danmaku", "bili_danmaku_official",
            "bili_danmaku_official_maicraft", "read_pingmu", "mainosaba"
        ])
    ]

    decision_providers = [
        pt for pt in PROVIDER_SCHEMA_REGISTRY.keys()
        if any(pt == dp for dp in ["maicore", "local_llm", "rule_engine", "mock"])
    ]

    output_providers = [pt for pt in PROVIDER_SCHEMA_REGISTRY.keys() if pt not in input_providers + decision_providers]

    return {
        "input": sorted(input_providers),
        "decision": sorted(decision_providers),
        "output": sorted(output_providers),
        "total": len(PROVIDER_SCHEMA_REGISTRY),
    }


def verify_no_enabled_field_in_schemas() -> list:
    """
    验证所有Provider Schema中都不包含'enabled'字段

    Returns:
        包含'enabled'字段的Schema列表（应为空）

    Note:
        这是架构约束测试。Provider的enabled状态由Manager统一管理，
        Schema中不应包含enabled字段。
    """
    schemas_with_enabled = []
    for provider_type, schema_class in PROVIDER_SCHEMA_REGISTRY.items():
        # 检查schema的所有字段
        if "enabled" in schema_class.model_fields:
            schemas_with_enabled.append(provider_type)

    return schemas_with_enabled


__all__ = [
    # Base schemas
    "BaseProviderConfig",
    # Input provider schemas（已迁移的自管理 Schema 不再导出）
    # Decision provider schemas（已迁移的自管理 Schema 不再导出）
    # Output provider schemas（所有已迁移的自管理 Schema 不再导出）
    "OUTPUT_PROVIDER_CONFIG_MAP",
    "get_output_provider_config",
    # Non-provider schemas
    "LoggingConfig",
    # Registry（空，所有Provider已迁移）
    "PROVIDER_SCHEMA_REGISTRY",
    # Helper functions
    "get_provider_schema",
    "validate_provider_config",
    "list_all_providers",
    "verify_no_enabled_field_in_schemas",
]
