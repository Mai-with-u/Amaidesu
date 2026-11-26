"""DashboardServer 端口回退绑定：空闲端口原样绑定、被占用回退下一端口、开发模式不回退、候选全占用报错"""

from __future__ import annotations

import errno
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from src.modules.dashboard.server import DashboardPortInUseError, DashboardServer, is_port_in_use_error
from src.modules.logging import get_logger

# 本机后台进程或并行测试进程会在 TCP 动态端口段抢位，Windows 上也可能撞到保留端口段（WinError 10013）。
# 预占冲突时换新起点整体重试有限次，耗尽才显式失败。
_RETRY_LIMIT = 20
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
    # 占住 base（模拟配置端口被外部程序占用）；并行测试环境下 base+1 可能被临近进程瞬时占用，
    # 回退应落在候选窗 (base, base + _PORT_FALLBACK_CANDIDATES) 内并同步更新 server.port
    with _held_contiguous_ports(1) as base:
        server = _make_server(base)
        with server._bind_socket() as sock:
            assert base < server.port < base + DashboardServer._PORT_FALLBACK_CANDIDATES
            assert sock.getsockname()[1] == server.port


def test_dev_mode_never_falls_back_because_the_vite_proxy_hardcodes_the_port() -> None:
    with _held_contiguous_ports(1) as base:
        server = _make_server(base, dev_mode=True)
        with pytest.raises(RuntimeError, match="开发模式"):
            server._bind_socket()
        assert server.port == base


def test_all_candidates_occupied_raises_without_rewriting_the_configured_port() -> None:
    with _held_contiguous_ports(DashboardServer._PORT_FALLBACK_CANDIDATES) as base:
        server = _make_server(base)
        with pytest.raises(DashboardPortInUseError, match="均被占用") as exc_info:
            server._bind_socket()
        assert isinstance(exc_info.value, RuntimeError)
        assert is_port_in_use_error(exc_info.value) is True
        assert server.port == base


def test_dev_mode_occupied_raises_port_in_use_error_subclass() -> None:
    """开发模式端口冲突抛出 DashboardPortInUseError（RuntimeError 子类），便于启动入口识别并中止双开进程。"""
    with _held_contiguous_ports(1) as base:
        server = _make_server(base, dev_mode=True)
        with pytest.raises(DashboardPortInUseError, match="开发模式") as exc_info:
            server._bind_socket()
        assert exc_info.value.port == base
        assert exc_info.value.dev_mode is True
        assert is_port_in_use_error(exc_info.value) is True


def test_is_port_in_use_error_recognizes_posix_and_windows_codes() -> None:
    """is_port_in_use_error 同时识别 DashboardPortInUseError、POSIX EADDRINUSE、Windows 10048 与其因果链。"""
    posix_err = OSError(errno.EADDRINUSE, "Address already in use")
    win_err = OSError(0, "Only one usage of each socket address is normally permitted")
    win_err.winerror = 10048  # type: ignore[attr-defined]
    wrapped = RuntimeError("bind failed")
    wrapped.__cause__ = win_err

    assert is_port_in_use_error(DashboardPortInUseError("busy")) is True
    assert is_port_in_use_error(posix_err) is True
    assert is_port_in_use_error(win_err) is True
    assert is_port_in_use_error(wrapped) is True
    assert is_port_in_use_error(RuntimeError("unrelated failure")) is False
    assert is_port_in_use_error(OSError(errno.ENOENT, "not found")) is False
