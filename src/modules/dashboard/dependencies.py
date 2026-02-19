"""
Dashboard 依赖注入

提供 FastAPI 依赖注入所需的服务器实例。
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

# 全局服务器实例
_dashboard_server: Optional["DashboardServer"] = None


def set_dashboard_server(server: "DashboardServer") -> None:
    """设置 Dashboard 服务器实例"""
    global _dashboard_server
    _dashboard_server = server


def get_dashboard_server() -> "DashboardServer":
    """获取 Dashboard 服务器实例（用于 FastAPI Depends）"""
    if _dashboard_server is None:
        raise NotImplementedError("Dashboard server not initialized")
    return _dashboard_server
