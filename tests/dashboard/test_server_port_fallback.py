"""DashboardServer 端口回退绑定测试"""

from __future__ import annotations

import socket

import pytest

from src.modules.dashboard.server import DashboardServer
from src.modules.logging import get_logger


def _make_server(port: int, dev_mode: bool = False) -> DashboardServer:
    """裸实例：_bind_socket 只依赖 host/port/dev_mode/logger 与类常量"""
    server = DashboardServer.__new__(DashboardServer)
    server.host = "127.0.0.1"
    server.port = port
    server.dev_mode = dev_mode
    server.logger = get_logger("TestPortFallback")
    return server


def _reserve_port() -> int:
    """取得一个空闲端口号（绑定后立即关闭，存在微小竞态但测试可接受）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _occupy(port: int) -> socket.socket:
    """占住一个端口（保持绑定不关闭），模拟被其他进程占用"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    return sock


def test_bind_socket_keeps_free_configured_port() -> None:
    """端口空闲时按配置端口绑定，self.port 不变"""
    port = _reserve_port()
    server = _make_server(port)
    sock = server._bind_socket()
    try:
        assert server.port == port
        assert sock.getsockname()[1] == port
    finally:
        sock.close()


def test_bind_socket_falls_back_to_next_port() -> None:
    """配置端口被占用时回退到下一端口并更新 self.port"""
    base = _reserve_port()
    try:
        probe = _occupy(base + 1)
    except OSError:
        pytest.skip(f"回退候选端口 {base + 1} 不可用，跳过")
    else:
        probe.close()
    occupier = _occupy(base)
    try:
        server = _make_server(base)
        sock = server._bind_socket()
        try:
            assert server.port == base + 1
            assert sock.getsockname()[1] == base + 1
        finally:
            sock.close()
    finally:
        occupier.close()


def test_bind_socket_dev_mode_does_not_fall_back() -> None:
    """开发模式不回退（vite 代理硬编码配置端口），直接报错"""
    base = _reserve_port()
    occupier = _occupy(base)
    try:
        server = _make_server(base, dev_mode=True)
        with pytest.raises(RuntimeError, match="开发模式"):
            server._bind_socket()
        assert server.port == base
    finally:
        occupier.close()


def test_bind_socket_raises_when_all_candidates_occupied() -> None:
    """全部候选端口被占用时报 RuntimeError，且不改写配置端口"""
    count = DashboardServer._PORT_FALLBACK_CANDIDATES
    base = _reserve_port()
    occupiers: list[socket.socket] = []
    try:
        for offset in range(count):
            try:
                occupiers.append(_occupy(base + offset))
            except OSError:
                pytest.skip(f"候选端口 {base + offset} 不可用，无法占满候选段，跳过")
        server = _make_server(base)
        with pytest.raises(RuntimeError, match="均被占用"):
            server._bind_socket()
        assert server.port == base
    finally:
        for sock in occupiers:
            sock.close()
