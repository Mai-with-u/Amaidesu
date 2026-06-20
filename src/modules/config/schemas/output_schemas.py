"""
输出Handler配置Schema

定义所有输出Handler的Pydantic配置模型。
"""

from typing import Any, Dict

from pydantic import BaseModel

from .base import BaseConfig


# 组件配置映射
OUTPUT_CONFIG_MAP: Dict[str, BaseConfig] = {}


def get_output_config(handler_type: str, config: Dict[str, Any]) -> BaseModel:
    """
    获取输出Handler配置对象

    Args:
        handler_type: Handler类型
        config: 配置字典

    Returns:
        对应的配置对象

    Raises:
        ValueError: 如果handler_type不支持
    """
    from src.modules.config.schemas import get_config_schema

    schema_class = get_config_schema(handler_type, phase="output")
    if not schema_class:
        raise ValueError(f"不支持的输出Handler类型: {handler_type}")

    return schema_class(**config)


__all__ = [
    "get_output_config",
    "OUTPUT_CONFIG_MAP",
]
