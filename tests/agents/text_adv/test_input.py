"""键鼠注入后端测试——只测假件、键名映射与注入封装接线，不真点键鼠。

真实后端为 ctypes 直调 Win32 API（Windows 专属）：接线断言经构造器注入
记录型按键发送器替身，任何平台都可跑且不触碰真实键鼠；非 Windows 构造
护栏单独验证；Fake 件测试不受平台影响。
"""

from __future__ import annotations

import sys

import pytest

from src.agents.text_adv.input import (
    FakeInputBackend,
    Win32InputBackend,
    _key_to_vk,
)
import src.agents.text_adv.input as input_mod


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


class TestKeyNameMapping:
    def test_named_keys_resolve(self) -> None:
        assert _key_to_vk("space") == 0x20
        assert _key_to_vk("enter") == 0x0D
        assert _key_to_vk("esc") == 0x1B
        assert _key_to_vk("f1") == 0x70
        assert _key_to_vk("f24") == 0x87
        assert _key_to_vk("ctrl") == 0x11
        assert _key_to_vk("shiftleft") == 0xA0
        assert _key_to_vk("win") == 0x5B
        assert _key_to_vk("printscreen") == 0x2C
        assert _key_to_vk("pageup") == 0x21
        assert _key_to_vk("pagedown") == 0x22
        assert _key_to_vk("delete") == 0x2E

    def test_letters_and_digits_resolve(self) -> None:
        assert _key_to_vk("a") == 0x41
        assert _key_to_vk("z") == 0x5A
        assert _key_to_vk("0") == 0x30
        assert _key_to_vk("9") == 0x39

    def test_unknown_names_return_none(self) -> None:
        assert _key_to_vk("notakey") is None
        assert _key_to_vk("") is None

    def test_modifier_aliases_map_to_left(self) -> None:
        assert _key_to_vk("winleft") == _key_to_vk("win")


class _RecordingSender:
    """测试用按键发送器替身：只记录调用，不触碰真实键鼠。"""

    def __init__(self) -> None:
        self.keys: list[tuple[int, int, bool]] = []
        self.chars: list[str] = []
        self.moves: list[tuple[int, int]] = []
        self.buttons: list[tuple[str, bool]] = []

    def map_vk_to_scan(self, vk: int) -> int:
        return vk * 2  # 可辨识的假扫描码，验证传递

    def send_key(self, vk: int, scan: int, key_up: bool) -> None:
        self.keys.append((vk, scan, key_up))

    def send_unicode_char(self, ch: str) -> None:
        self.chars.append(ch)

    def move_cursor(self, x: int, y: int) -> None:
        self.moves.append((x, y))

    def send_mouse_button(self, button: str, key_up: bool) -> None:
        self.buttons.append((button, key_up))


class TestWin32InputBackend:
    """真实接线断言：底层注入通道注入替身，不触碰真实键鼠。"""

    def _make_backend(self) -> tuple[Win32InputBackend, _RecordingSender]:
        sender = _RecordingSender()
        return Win32InputBackend(sender=sender), sender

    def test_press_named_key_sends_down_then_up(self) -> None:
        backend, sender = self._make_backend()
        backend.press("space")
        assert sender.keys == [(0x20, 0x40, False), (0x20, 0x40, True)]

    def test_press_single_char_uses_unicode_path(self) -> None:
        backend, sender = self._make_backend()
        backend.press("你")
        assert sender.chars == ["你"]

    def test_press_unknown_multi_char_name_raises(self) -> None:
        backend, _ = self._make_backend()
        with pytest.raises(ValueError, match="不支持的键名"):
            backend.press("notakey")

    def test_hold_releases_in_finally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend, sender = self._make_backend()

        def boom(_duration_ms: float) -> None:
            raise TimeoutError("模拟睡眠被打断")

        monkeypatch.setattr(input_mod.time, "sleep", boom)
        with pytest.raises(TimeoutError):
            backend.hold("ctrl", 100)
        assert sender.keys == [(0x11, 0x22, False), (0x11, 0x22, True)]

    def test_click_moves_cursor_then_clicks(self) -> None:
        backend, sender = self._make_backend()
        backend.click(10, 20, button="right")
        assert sender.moves == [(10, 20)]
        assert sender.buttons == [("right", False), ("right", True)]

    def test_click_unknown_button_raises(self) -> None:
        backend, _ = self._make_backend()
        with pytest.raises(ValueError, match="不支持的鼠标按钮"):
            backend.click(1, 2, button="side")


class TestNonWindowsGuard:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="仅非 Windows 平台验证构造护栏",
    )
    def test_init_raises_on_non_windows(self) -> None:
        with pytest.raises(RuntimeError, match="仅支持 Windows"):
            Win32InputBackend()
