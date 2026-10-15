"""DashboardServer 端口回退绑定：空闲端口原样绑定、被占用回退下一端口、开发模式不回退、候选全占用报错"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager

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


@contextmanager
def _occupied(*ports: int) -> Iterator[None]:
    """占住端口（保持绑定不关闭），模拟被其他进程占用；预占失败说明环境异常，显式失败不静默跳过"""
    socks: list[socket.socket] = []
    try:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            socks.append(sock)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                pytest.fail(f"预占候选端口 {port} 失败，测试环境异常：{exc}")
        yield
    finally:
        for sock in socks:
            sock.close()


def test_free_configured_port_is_kept() -> None:
    port = _reserve_port()
    server = _make_server(port)
    with server._bind_socket() as sock:
        assert server.port == port and sock.getsockname()[1] == port


def test_occupied_port_falls_back_to_the_next_one_and_updates_the_server_port() -> None:
    base = _reserve_port()
    with _occupied(base):
        server = _make_server(base)
        with server._bind_socket() as sock:
            assert server.port == base + 1 and sock.getsockname()[1] == base + 1


def test_dev_mode_never_falls_back_because_the_vite_proxy_hardcodes_the_port() -> None:
    base = _reserve_port()
    with _occupied(base):
        server = _make_server(base, dev_mode=True)
        with pytest.raises(RuntimeError, match="开发模式"):
            server._bind_socket()
        assert server.port == base


def test_all_candidates_occupied_raises_without_rewriting_the_configured_port() -> None:
    base = _reserve_port()
    with _occupied(*(base + offset for offset in range(DashboardServer._PORT_FALLBACK_CANDIDATES))):
        server = _make_server(base)
        with pytest.raises(RuntimeError, match="均被占用"):
            server._bind_socket()
        assert server.port == base
