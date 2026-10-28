"""窗口后端：假件的注入结果与几何比较、pygetwindow 后端对替身窗口的包装（不触碰真实窗口）。"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from src.agents.text_adv.window import FakeWindowBackend, PyGetWindowBackend, WindowInfo

# pygetwindow 仅提供 Windows/macOS 实现（无 Linux 分支），无显示环境无法实例化；
# 这些用例在 Windows 本地真实执行，CI（Linux）按平台条件跳过。
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="pygetwindow 仅支持 Windows/macOS，Linux CI 无此平台实现"
)

_RECT = (10, 20, 800, 600)


def _info(handle: Any = None) -> WindowInfo:
    return WindowInfo(title="测试窗口", left=10, top=20, width=800, height=600, handle=handle)


def _stub(left: int = 5, *, fail_activate: bool = False) -> SimpleNamespace:
    """duck-typed pygetwindow 窗口对象。"""

    def activate() -> None:
        if fail_activate:
            raise RuntimeError("激活失败")

    return SimpleNamespace(title="游戏窗口", left=left, top=6, width=7, height=8, handle=12345, activate=activate)


def test_fake_backend_finds_by_injection_and_records_keywords() -> None:
    backend = FakeWindowBackend(found=True, rect=_RECT)
    info = backend.find("测试窗口")

    assert (info.title, info.left, info.top, info.width, info.height) == ("测试窗口", *_RECT)
    assert FakeWindowBackend(found=False).find("不存在的窗口") is None
    assert backend.find_calls == ["测试窗口"]


def test_fake_backend_returns_injected_focus_and_foreground_results() -> None:
    for focus_ok in (True, False):
        backend = FakeWindowBackend(focus_ok=focus_ok, foreground=focus_ok)
        info = backend.find("任意")
        assert backend.focus(info) is focus_ok and backend.is_foreground(info) is focus_ok


def test_fake_backend_detects_a_moved_window() -> None:
    backend = FakeWindowBackend(found=True, rect=_RECT)
    info = backend.find("任意")

    assert backend.moved_since(info, _RECT) is False
    backend.set_rect((30, 40, 800, 600))
    assert backend.moved_since(info, _RECT) is True


def test_pygetwindow_find_wraps_the_match_and_misses_return_none(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub(left=1)
    monkeypatch.setattr("pygetwindow.getWindowsWithTitle", lambda keyword: [stub] if keyword in stub.title else [])
    backend = PyGetWindowBackend()

    info = backend.find("游戏")
    assert (info.title, info.left, info.top, info.width, info.height) == ("游戏窗口", 1, 6, 7, 8)
    assert info.handle is stub
    assert backend.find("绝不存在的窗口名_xyzzy") is None


@pytest.mark.parametrize(
    ("handle", "expected"),
    [(_stub(), True), (_stub(fail_activate=True), False), ("not-a-window", False)],
    ids=["ok", "activate_raises", "wrong_handle_type"],
)
def test_pygetwindow_focus_reports_success_and_swallows_failures(handle: Any, expected: bool) -> None:
    assert PyGetWindowBackend().focus(_info(handle=handle)) is expected


def test_pygetwindow_rect_and_moved_since_read_the_live_geometry() -> None:
    stub = _stub()
    backend = PyGetWindowBackend()
    info = _info(handle=stub)

    assert backend.rect(info) == (5, 6, 7, 8) and backend.moved_since(info, (5, 6, 7, 8)) is False
    stub.left = 50
    assert backend.moved_since(info, (5, 6, 7, 8)) is True
