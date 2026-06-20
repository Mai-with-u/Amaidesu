"""
Dashboard API 路由模块

包含所有 REST API 端点。
"""

from src.modules.dashboard.api import components, config, debug, messages, system

__all__ = ["system", "components", "messages", "config", "debug"]
