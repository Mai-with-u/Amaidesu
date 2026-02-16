"""
Dashboard 模块 - Web 管理界面

提供实时监控、Provider 管理、配置编辑等功能。
"""

from src.modules.dashboard.config import DashboardConfig
from src.modules.dashboard.server import DashboardServer

__all__ = ["DashboardServer", "DashboardConfig"]
