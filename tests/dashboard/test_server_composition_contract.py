"""DashboardServer 组装层消费契约测试

main.py 作为组装根直接读取 server 的公开属性做装配决策；
本文件固定这些属性的存在性与语义，防止重构挪动后组装层启动即崩。
"""

from __future__ import annotations

from src.modules.dashboard.server import DashboardServer
from src.modules.dashboard.widget.routes import WidgetGateway


def test_widget_service_delegates_to_gateway() -> None:
    """组装层经 widget_service 判断是否注册字幕后端；gateway 未装载时为 None"""
    server = DashboardServer.__new__(DashboardServer)
    server.widget_gateway = WidgetGateway()
    assert server.widget_service is None
