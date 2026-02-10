"""
Amaidesu 日志模块

提供日志记录功能。
"""

# 导出核心组件
from .logger import configure_from_config, get_logger

__all__ = [
    "configure_from_config",
    "get_logger",
]
