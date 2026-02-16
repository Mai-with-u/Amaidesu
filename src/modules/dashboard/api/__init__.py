"""
Dashboard API 路由模块

包含所有 REST API 端点。
"""

from src.modules.dashboard.api import config, debug, messages, providers, system

__all__ = ["system", "providers", "messages", "config", "debug"]
