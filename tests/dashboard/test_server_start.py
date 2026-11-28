"""DashboardServer 启动完成的含义：start() 返回时端口必须已经能接受连接

调用方在 start() 返回后紧接着就会来连——自动打开的浏览器、健康探测、测试里的客户端。
提供服务的是后台任务，如果 start() 返回时端口还没开始监听，Linux 上连接会被直接拒绝。
"""

from __future__ import annotations

import socket
from pathlib import Path

from src.modules.dashboard.server import DashboardConfig, DashboardServer
from src.modules.events import EventBus


def _free_port() -> int:
    """取一个当前空闲的端口号（探针绑定后立即关闭；被抢先占用时 start 会回退到相邻端口）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def test_start_returns_after_the_port_accepts_connections(tmp_path: Path) -> None:
    """start() 一返回、不让出事件循环，客户端就能完成 TCP 连接。"""
    bus = EventBus()
    server = DashboardServer(
        event_bus=bus,
        config_service=object(),  # type: ignore[arg-type]
        dashboard_config=DashboardConfig(host="127.0.0.1", port=_free_port(), auto_open_browser=False),
    )
    await server.start()
    try:
        # 阻塞式连接，全程不让出事件循环：后台服务任务此时还没机会跑，
        # 连接能否完成只取决于 start() 返回时端口是否已经在监听
        with socket.create_connection(("127.0.0.1", server.port), timeout=1.0):
            pass
    finally:
        await server.stop()
        await server.cleanup()
        await bus.cleanup()
