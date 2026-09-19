"""
Dashboard API 路由模块

包含所有 REST API 端点。
"""

from src.modules.dashboard.api import (
    rundown,
    components,
    config,
    debug,
    sessions,
    simulator,
    system,
    viewers,
    vision,
)

__all__ = [
    "system",
    "components",
    "config",
    "debug",
    "simulator",
    "rundown",
    "sessions",
    "viewers",
    "vision",
]
