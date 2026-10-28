"""DashboardServer 端口回退绑定：空闲端口原样绑定、被占用回退下一端口、开发模式不回退、候选全占用报错"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from src.modules.dashboard.server import DashboardServer
from src.modules.logging import get_logger

# 本机后台进程会在 TCP 动态端口段抢位：取号（绑定后关闭）与再次绑定之间存在
# 空窗。预占冲突（Windows 10048）时换新起点整体重试有限次，耗尽才显式失败。
_RETRY_LIMIT = 5
_RETRY_INTERVAL_S = 0.01


def _make_server(port: int, dev_mode: bool = False) -> DashboardServer:
    """裸实例：_bind_socket 只依赖 host/port/dev_mode/logger 与类常量"""
    server = DashboardServer.__new__(DashboardServer)
    server.host = "127.0.0.1"
    server.port = port
    server.dev_mode = dev_mode
    server.logger = get_logger("TestPortFallback")
    return server


def _reserve_port() -> int:
    """取得一个空闲端口号（绑定后立即关闭）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _held_contiguous_ports(count: int) -> Iterator[int]:
    """占住 count 个相邻端口（全部保持绑定不关闭，取号与占位无空窗），yield 起始端口号

    预占遇到瞬时占用冲突（本机后台进程抢位）时关闭已占 socket、换新起点整体重试；
    重试耗尽说明环境异常，显式失败不静默跳过。
    """
    last_error: OSError | None = None
    for _ in range(_RETRY_LIMIT):
        socks: list[socket.socket] = []
        try:
            for _offset in range(count):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                socks.append(sock)
                sock.bind(("127.0.0.1", 0) if _offset == 0 else ("127.0.0.1", socks[0].getsockname()[1] + _offset))
        except OSError as exc:
            last_error = exc
            for sock in socks:
                sock.close()
            time.sleep(_RETRY_INTERVAL_S)
            continue
        try:
            yield int(socks[0].getsockname()[1])
        finally:
            for sock in socks:
                sock.close()
        return
    pytest.fail(
        f"连续 {_RETRY_LIMIT} 次预占 {count} 个相邻端口均失败（末次原因：{last_error}），测试环境动态端口竞争异常"
    )


def test_free_configured_port_is_kept() -> None:
    for _ in range(_RETRY_LIMIT):
        port = _reserve_port()
        server = _make_server(port)
        with server._bind_socket() as sock:
            if sock.getsockname()[1] == port:
                assert server.port == port
                break
        # 取到的端口在绑定前被本机后台进程抢占：换新端口整体重试
        time.sleep(_RETRY_INTERVAL_S)
    else:
        pytest.fail(f"连续 {_RETRY_LIMIT} 次取到的空闲端口均在绑定前被抢占，测试环境动态端口竞争异常")


def test_occupied_port_falls_back_to_the_next_one_and_updates_the_server_port() -> None:
    # 占住 base（模拟配置端口被其他进程占用），base+1 保持空闲供回退
    with _held_contiguous_ports(1) as base:
        server = _make_server(base)
        with server._bind_socket() as sock:
            assert server.port == base + 1 and sock.getsockname()[1] == base + 1


def test_dev_mode_never_falls_back_because_the_vite_proxy_hardcodes_the_port() -> None:
    with _held_contiguous_ports(1) as base:
        server = _make_server(base, dev_mode=True)
        with pytest.raises(RuntimeError, match="开发模式"):
            server._bind_socket()
        assert server.port == base


def test_all_candidates_occupied_raises_without_rewriting_the_configured_port() -> None:
    with _held_contiguous_ports(DashboardServer._PORT_FALLBACK_CANDIDATES) as base:
        server = _make_server(base)
        with pytest.raises(RuntimeError, match="均被占用"):
            server._bind_socket()
        assert server.port == base
