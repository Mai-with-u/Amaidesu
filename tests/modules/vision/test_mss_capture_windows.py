"""真实 Win32 DPI 接口集成测试，由 Windows CI 执行。

跨平台模拟测试另外覆盖状态切换；这里保留真实系统接口的回归验证。
"""

import ctypes
import sys

import pytest

from src.modules.vision.mss_capture import _per_monitor_dpi_thread

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="真实 Win32 API 由 Windows CI 验证")


def _current_thread_dpi_context() -> int | None:
    user32 = ctypes.windll.user32
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetThreadDpiAwarenessContext.argtypes = []
    context = user32.GetThreadDpiAwarenessContext()
    return int(context) if context else None


def _assert_per_monitor_v2() -> None:
    compare = ctypes.windll.user32.AreDpiAwarenessContextsEqual
    compare.restype = ctypes.c_bool
    compare.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    assert compare(_current_thread_dpi_context(), ctypes.c_void_p(-4))


def test_native_context_restored_after_exit() -> None:
    """进入时实际切到 per-monitor v2，退出后还原调用线程。"""
    before = _current_thread_dpi_context()
    assert before is not None
    with _per_monitor_dpi_thread():
        _assert_per_monitor_v2()
    assert _current_thread_dpi_context() == before


def test_native_nested_context_restore() -> None:
    """内层退出不能提前恢复外层的原始 DPI 上下文。"""
    before = _current_thread_dpi_context()
    assert before is not None
    with _per_monitor_dpi_thread():
        _assert_per_monitor_v2()
        with _per_monitor_dpi_thread():
            _assert_per_monitor_v2()
        _assert_per_monitor_v2()
    assert _current_thread_dpi_context() == before
