"""
配置管理模块

此模块提供统一的配置管理功能，包括:
- 配置文件加载和合并
- 从 Pydantic Schemas 自动生成配置文件
- 配置版本检查和更新

主要子模块:
- generator: 从 Pydantic Schemas 生成 TOML 配置文件
"""

from src.core.config.generator import (
    generate_toml,
    ensure_provider_config,
    generate_config_dict,
    merge_config_with_tomli_w,
    batch_ensure_provider_configs,
)

__all__ = [
    "generate_toml",
    "ensure_provider_config",
    "generate_config_dict",
    "merge_config_with_tomli_w",
    "batch_ensure_provider_configs",
]
