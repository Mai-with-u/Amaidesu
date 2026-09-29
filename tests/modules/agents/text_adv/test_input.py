"""键鼠注入后端测试——只测假件与配置断言，不真点键鼠。

pyautogui 的依赖 mouseinfo 在部分 CI runner CPU 上 import 期原生崩溃
（SIGILL），且该库无 Linux 支持——真实后端的接线断言仅在有显示的
Windows 上执行，其余平台跳过；Fake 件测试不受影响。
"""

from __future__ import annotations

import sys

import pytest

from src.agents.text_adv.input import (
    FakeInputBackend,
    PyAutoGuiInputBackend,
)


class TestFakeInputBackend:
    def test_hold_records_down_then_up(self) -> None:
        backend = FakeInputBackend()
        backend.hold("ctrl", 500)
        assert backend.calls == ["keyDown:ctrl", "keyUp:ctrl"]

    def test_press_records_call(self) -> None:
        backend = FakeInputBackend()
        backend.press("space")
        assert backend.calls == ["press:space"]

    def test_click_records_with_default_button(self) -> None:
        backend = FakeInputBackend()
        backend.click(100, 200)
        assert backend.calls == ["click:100,200,left"]

    def test_click_records_custom_button(self) -> None:
        backend = FakeInputBackend()
        backend.click(100, 200, button="right")
        assert backend.calls == ["click:100,200,right"]

    def test_sequence_order_preserved(self) -> None:
        backend = FakeInputBackend()
        backend.press("space")
        backend.hold("ctrl", 100)
        backend.click(1, 2)
        assert backend.calls == [
            "press:space",
            "keyDown:ctrl",
            "keyUp:ctrl",
            "click:1,2,left",
        ]


class TestPyAutoGuiInputBackendConfig:
    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="pyautogui/mouseinfo 在 Linux CI import 期原生崩溃",
    )
    def test_init_disables_failsafe_and_pause(self) -> None:
        import pyautogui

        PyAutoGuiInputBackend()
        assert pyautogui.FAILSAFE is False
        assert pyautogui.PAUSE == 0
