"""
Dashboard API 路由模块

包含所有 REST API 端点。
"""

from src.modules.dashboard.api import (
    agenda,
    components,
    config,
    debug,
    sessions,
    simulator,
    system,
    viewers,
)

__all__ = [
    "system",
    "components",
    "config",
    "debug",
    "simulator",
    "agenda",
    "sessions",
    "viewers",
]
