"""Pydantic schemas for configuration validation."""

from typing import Dict, Optional, Type

from pydantic import BaseModel

# Base schema
from .base import BaseConfig

# Non-component schemas (system-wide configurations)
from .logging import LoggingConfig

# 组件 Schema Registry — 必须放在所有 import 之前，
# 因为 @collector/@decider/@handler 装饰器在模块加载时立即调用 register_config_schema，
# 若 registry 尚未定义则装饰器会静默吞掉 ImportError，导致 CONFIG_SCHEMA_REGISTRY 始终为空。
CONFIG_SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {}


def register_config_schema(type: str, schema_class: Type[BaseModel]) -> None:
    """注册组件的配置 Schema"""
    CONFIG_SCHEMA_REGISTRY[type] = schema_class


# Output handler schemas — 延迟导入以避免循环依赖，见上方 registry 注释
from .output_schemas import (  # noqa: E402
    OutputConfig,
    OutputHandlersConfig,
    OutputPipelinesConfig,
    get_output_config,
)

# Input stage schemas (Task 8)
from .input_schemas import (  # noqa: E402
    InputCollectorsConfig,
    InputConfig,
    InputPipelinesConfig,
    get_input_collector_config,
)

# Decision stage schemas (Task 10)
from .decision_schemas import (  # noqa: E402
    AmaidesuDeciderConfigSchema,
    CommandDeciderConfigSchema,
    DecisionConfig,
    DecisionDecidersConfig,
    DecisionPipelinesConfig,
    LLMDeciderConfigSchema,
    MaiBotDeciderConfigSchema,
    ReplayDeciderConfigSchema,
)


def get_config_schema(type: str, phase: Optional[str] = None) -> Type[BaseModel]:
    """
    获取组件的 Schema 类

    Args:
        type: Collector/Decider/Handler 类型标识
        phase: 组件所处阶段（可选，仅用于错误信息）

    Returns:
        对应的 Schema 类

    Raises:
        KeyError: 如果 type 未注册
    """
    schema = CONFIG_SCHEMA_REGISTRY.get(type)
    if schema is not None:
        return schema

    raise KeyError(f"未注册的 Collector/Decider/Handler 类型: {type} (phase: {phase})")


def validate_config(type: str, config: dict) -> BaseModel:
    """
    验证组件配置

    Args:
        type: Collector/Decider/Handler 类型
        config: 配置字典

    Returns:
        验证后的配置对象

    Raises:
        KeyError: 如果 type 未注册
        ValidationError: 如果配置验证失败
    """
    schema_class = get_config_schema(type)
    # 通过 from_dict 加载，自动剥离未知字段（配合 extra="forbid"）
    if hasattr(schema_class, "from_dict"):
        return schema_class.from_dict(config)
    return schema_class(**config)


def list_registered_schemas() -> dict:
    """
    列出所有已注册的 Schema

    Returns:
        包含分类列表的字典:
        {
            "input": [],
            "decision": [],
            "output": sorted(CONFIG_SCHEMA_REGISTRY.keys()),
            "total": len(CONFIG_SCHEMA_REGISTRY),
        }
    """
    input_keys = sorted(
        name
        for name, cls in CONFIG_SCHEMA_REGISTRY.items()
        if isinstance(cls, type) and issubclass(cls, BaseConfig) and getattr(cls, "__phase__", "") == "input"
    )
    decision_keys = sorted(
        name
        for name, cls in CONFIG_SCHEMA_REGISTRY.items()
        if isinstance(cls, type) and issubclass(cls, BaseConfig) and getattr(cls, "__phase__", "") == "decision"
    )
    output_keys = sorted(
        name
        for name, cls in CONFIG_SCHEMA_REGISTRY.items()
        if isinstance(cls, type) and issubclass(cls, BaseConfig) and getattr(cls, "__phase__", "") == "output"
    )
    return {
        "input": input_keys,
        "decision": decision_keys,
        "output": output_keys,
        "total": len(CONFIG_SCHEMA_REGISTRY),
    }


def verify_no_enabled_field_in_schemas() -> list:
    """
    验证所有组件 Schema 中都不包含 'enabled' 字段

    Returns:
        包含 'enabled' 字段的 Schema 列表（应为空）
    """
    schemas_with_enabled = []

    for name, schema_class in CONFIG_SCHEMA_REGISTRY.items():
        if hasattr(schema_class, "model_fields") and "enabled" in schema_class.model_fields:
            schemas_with_enabled.append(name)

    return schemas_with_enabled


__all__ = [
    # Base schemas
    "BaseConfig",
    # Output handler schemas
    "OutputConfig",
    "OutputHandlersConfig",
    "OutputPipelinesConfig",
    "get_output_config",
    # Input collector schemas
    "InputConfig",
    "InputCollectorsConfig",
    "InputPipelinesConfig",
    "get_input_collector_config",
    # Decision decider schemas
    "LLMDeciderConfigSchema",
    "MaiBotDeciderConfigSchema",
    "AmaidesuDeciderConfigSchema",
    "CommandDeciderConfigSchema",
    "ReplayDeciderConfigSchema",
    "DecisionDecidersConfig",
    "DecisionPipelinesConfig",
    "DecisionConfig",
    # Non-component schemas
    "LoggingConfig",
    # Registry
    "CONFIG_SCHEMA_REGISTRY",
    # Helper functions
    "get_config_schema",
    "validate_config",
    "list_registered_schemas",
    "verify_no_enabled_field_in_schemas",
    "register_config_schema",
]
