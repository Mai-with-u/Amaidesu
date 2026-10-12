"""窗口后端测试——只测假件与不触碰真实窗口的路径。"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from src.agents.text_adv.window import (
    FakeWindowBackend,
    PyGetWindowBackend,
    WindowInfo,
)

# pygetwindow 仅提供 Windows/macOS 实现（无 Linux 分支），无显示环境无法
# 实例化；这些用例在 Windows 本地真实执行，CI（Linux）按平台条件跳过。
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="pygetwindow 仅支持 Windows/macOS，Linux CI 无此平台实现",
)


def _make_info(handle: Any = None) -> WindowInfo:
    return WindowInfo(
        title="测试窗口",
        left=10,
        top=20,
        width=800,
        height=600,
        handle=handle,
    )


class TestFakeWindowBackend:
    def test_find_returns_none_when_not_found(self) -> None:
        backend = FakeWindowBackend(found=False)
        assert backend.find("不存在的窗口") is None

    def test_find_returns_injected_window(self) -> None:
        backend = FakeWindowBackend(found=True, rect=(10, 20, 800, 600))
        info = backend.find("测试窗口")
        assert info is not None
        assert info.title == "测试窗口"
        assert (info.left, info.top, info.width, info.height) == (10, 20, 800, 600)

    def test_focus_returns_injected_result(self) -> None:
        backend = FakeWindowBackend(focus_ok=False)
        info = backend.find("任意")
        assert info is not None
        assert backend.focus(info) is False

        backend_ok = FakeWindowBackend(focus_ok=True)
        info_ok = backend_ok.find("任意")
        assert info_ok is not None
        assert backend_ok.focus(info_ok) is True

    def test_is_foreground_returns_injected_result(self) -> None:
        backend = FakeWindowBackend(foreground=False)
        info = backend.find("任意")
        assert info is not None
        assert backend.is_foreground(info) is False

    def test_moved_since_true_when_rect_changed(self) -> None:
        backend = FakeWindowBackend(found=True, rect=(10, 20, 800, 600))
        info = backend.find("任意")
        assert info is not None
        assert backend.moved_since(info, (10, 20, 800, 600)) is False
        backend.set_rect((30, 40, 800, 600))
        assert backend.moved_since(info, (10, 20, 800, 600)) is True

    def test_find_records_keyword(self) -> None:
        backend = FakeWindowBackend(found=False)
        backend.find("关键字")
        assert backend.find_calls == ["关键字"]


class _StubPyGetWindowWindow:
    """duck-typed pygetwindow 窗口对象，避免测试触碰真实窗口。"""

    def __init__(
        self,
        title: str,
        left: int,
        top: int,
        width: int,
        height: int,
        handle: int,
        raise_on_activate: bool = False,
    ) -> None:
        self.title = title
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.handle = handle
        self._raise_on_activate = raise_on_activate

    def activate(self) -> None:
        if self._raise_on_activate:
            raise RuntimeError("激活失败")


class TestPyGetWindowBackend:
    def test_find_returns_none_when_no_match(self) -> None:
        backend = PyGetWindowBackend()
        assert backend.find("绝不存在的窗口名_xyzzy") is None

    def test_find_wraps_match_into_window_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _StubPyGetWindowWindow("游戏窗口", 1, 2, 3, 4, handle=12345)
        monkeypatch.setattr("pygetwindow.getWindowsWithTitle", lambda keyword: [stub] if keyword in stub.title else [])
        backend = PyGetWindowBackend()
        info = backend.find("游戏")
        assert info is not None
        assert info.title == "游戏窗口"
        assert (info.left, info.top, info.width, info.height) == (1, 2, 3, 4)
        assert info.handle is stub

    def test_focus_failure_returns_false(self) -> None:
        stub = _StubPyGetWindowWindow("w", 0, 0, 1, 1, handle=1, raise_on_activate=True)
        backend = PyGetWindowBackend()
        assert backend.focus(_make_info(handle=stub)) is False

    def test_focus_success_returns_true(self) -> None:
        stub = _StubPyGetWindowWindow("w", 0, 0, 1, 1, handle=1)
        backend = PyGetWindowBackend()
        assert backend.focus(_make_info(handle=stub)) is True

    def test_focus_with_wrong_handle_type_returns_false(self) -> None:
        backend = PyGetWindowBackend()
        assert backend.focus(_make_info(handle="not-a-window")) is False

    def test_rect_reads_current_geometry(self) -> None:
        stub = _StubPyGetWindowWindow("w", 5, 6, 7, 8, handle=1)
        backend = PyGetWindowBackend()
        assert backend.rect(_make_info(handle=stub)) == (5, 6, 7, 8)

    def test_moved_since_detects_position_change(self) -> None:
        stub = _StubPyGetWindowWindow("w", 5, 6, 7, 8, handle=1)
        backend = PyGetWindowBackend()
        info = _make_info(handle=stub)
        assert backend.moved_since(info, (5, 6, 7, 8)) is False
        stub.left = 50
        assert backend.moved_since(info, (5, 6, 7, 8)) is True
