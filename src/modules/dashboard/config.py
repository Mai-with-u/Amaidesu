"""
Dashboard 配置模型

统一由 ``src.modules.config.core_schemas`` 维护，
本文件仅做重导出，避免破坏既有调用方 ``from src.modules.dashboard.config import DashboardConfig``。
"""

from src.modules.config.core_schemas import (
    DashboardConfig,
)

__all__ = ["DashboardConfig"]
