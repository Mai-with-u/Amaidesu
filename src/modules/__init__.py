"""
Amaidesu 基础模块层

包含所有可复用的基础设施模块。
"""

# 导出核心组件
from .registry import ProviderRegistry

__all__ = [
    "ProviderRegistry",
]
