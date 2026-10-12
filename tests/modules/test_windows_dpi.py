"""Windows 进程 DPI 感知设置测试。

DPI 感知决定屏幕坐标口径：声明为 per-monitor-v2 时 Tk 的窗口位置/鼠标屏幕
坐标与 Windows 枚举出的显示器矩形同为物理像素，字幕窗才能被拖到副屏。
这里的用例只验证"声明顺序与失败降级"——真实口径由 Tk 端到端行为决定。
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest

from src.modules import windows_dpi


class _FakeFunc:
    """记录调用的假 DLL 函数。"""

    def __init__(self, result: Any = True) -> None:
        self.result = result
        self.calls: List[Any] = []
        self.restype: Any = None
        self.argtypes: Any = None

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.result

    @property
    def passed_values(self) -> List[int]:
        """调用参数里 ctypes 指针的取值。

        ``ctypes.c_void_p(-4).value`` 按无符号解释（``2**64-4``），这里折回
        有符号值，断言才能直写 ``-4`` 这样的上下文常量。
        """
        values: List[int] = []
        for (arg,) in self.calls:
            raw = getattr(arg, "value", arg)
            if isinstance(raw, int) and raw >= 2**63:
                raw -= 2**64
            values.append(raw)
        return values


class _FakeUser32:
    def __init__(self, context_result: Any = True, has_context_api: bool = True) -> None:
        self.set_context = _FakeFunc(context_result)
        if has_context_api:
            self.SetProcessDpiAwarenessContext = self.set_context


class _FakeShcore:
    def __init__(self, hr: int = 0) -> None:
        self.set_awareness = _FakeFunc(hr)
        self.SetProcessDpiAwareness = self.set_awareness


def _patch_dlls(
    monkeypatch: pytest.MonkeyPatch,
    user32: Optional[_FakeUser32],
    shcore: Optional[_FakeShcore],
) -> None:
    monkeypatch.setattr(windows_dpi, "_user32", lambda: user32)
    monkeypatch.setattr(windows_dpi, "_shcore", lambda: shcore)


def test_uses_context_api_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """首选 Win10 1607+ 的上下文接口，并请求 per-monitor-v2（-4）。"""
    user32 = _FakeUser32()
    shcore = _FakeShcore()
    _patch_dlls(monkeypatch, user32, shcore)

    assert windows_dpi.setup_process_dpi_awareness() is True
    assert user32.set_context.passed_values == [-4]
    assert shcore.set_awareness.calls == [], "上下文接口已成功，不应再走旧接口"


def test_falls_back_to_legacy_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """上下文接口缺失（老系统）时回退到进程感知枚举（1 = per-monitor）。"""
    user32 = _FakeUser32(has_context_api=False)
    shcore = _FakeShcore(hr=0)
    _patch_dlls(monkeypatch, user32, shcore)

    assert windows_dpi.setup_process_dpi_awareness() is True
    assert shcore.set_awareness.calls == [(1,)]


def test_reports_failure_when_both_apis_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个接口都失败（感知已被清单等其他机制定死）时返回 False，不抛异常。

    失败不是错误路径：进程口径已固定，调用方继续跑，只是坐标口径不由本模块
    负责；返回值供日志与排查使用。
    """
    user32 = _FakeUser32(context_result=False)
    shcore = _FakeShcore(hr=-2147024891)  # E_ACCESSDENIED
    _patch_dlls(monkeypatch, user32, shcore)

    assert windows_dpi.setup_process_dpi_awareness() is False
    assert user32.set_context.passed_values == [-4]
    assert shcore.set_awareness.calls == [(1,)]


def test_handles_missing_dlls(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 Windows（两个句柄都为 None）时静默返回 False，不触碰 ctypes。"""
    _patch_dlls(monkeypatch, None, None)

    assert windows_dpi.setup_process_dpi_awareness() is False


def test_legacy_call_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧接口调用本身抛 OSError 时按失败处理，不向上冒泡。"""
    user32 = _FakeUser32(has_context_api=False)

    class _ExplodingShcore:
        @staticmethod
        def SetProcessDpiAwareness(_awareness: int) -> int:
            raise OSError("shcore 不可用")

    monkeypatch.setattr(windows_dpi, "_user32", lambda: user32)
    monkeypatch.setattr(windows_dpi, "_shcore", lambda: _ExplodingShcore())

    assert windows_dpi.setup_process_dpi_awareness() is False
