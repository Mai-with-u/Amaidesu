"""键鼠注入后端测试——只测假件、键名映射与注入封装接线，不真点键鼠。

真实后端为 ctypes 直调 Win32 API（Windows 专属）：非 Windows 平台构造
即抛错，故真实接线断言仅在有显示的 Windows 上执行且全部走 monkeypatch
的薄封装，不触碰真实键鼠；Fake 件测试不受平台影响。
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


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Win32InputBackend 为 Windows 专属，非 Windows 构造即抛错",
)
class TestWin32InputBackend:
    """真实接线断言：user32 薄封装全部 monkeypatch，不触碰真实键鼠。"""

    def _make_backend(self) -> Win32InputBackend:
        return Win32InputBackend()

    def test_press_named_key_sends_down_then_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: list[tuple[int, int, bool]] = []
        monkeypatch.setattr(input_mod, "_send_key", lambda vk, scan, key_up: sent.append((vk, scan, key_up)))
        backend = self._make_backend()
        backend.press("space")
        assert sent == [(0x20, sent[0][1], False), (0x20, sent[0][1], True)]

    def test_press_single_char_uses_unicode_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        chars: list[str] = []
        monkeypatch.setattr(input_mod, "_send_unicode_char", lambda ch: chars.append(ch))
        backend = self._make_backend()
        backend.press("你")
        assert chars == ["你"]

    def test_press_unknown_multi_char_name_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = self._make_backend()
        with pytest.raises(ValueError, match="不支持的键名"):
            backend.press("notakey")

    def test_hold_releases_in_finally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[tuple[int, bool]] = []
        monkeypatch.setattr(
            input_mod,
            "_send_key",
            lambda vk, scan, key_up: events.append((vk, key_up)),
        )

        def boom(_duration_ms: float) -> None:
            raise TimeoutError("模拟睡眠被打断")

        monkeypatch.setattr(input_mod.time, "sleep", boom)
        backend = self._make_backend()
        with pytest.raises(TimeoutError):
            backend.hold("ctrl", 100)
        assert events == [(0x11, False), (0x11, True)]

    def test_click_moves_cursor_then_clicks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        moves: list[tuple[int, int]] = []
        clicks: list[tuple[str, bool]] = []
        monkeypatch.setattr(input_mod, "_move_cursor", lambda x, y: moves.append((x, y)))
        monkeypatch.setattr(
            input_mod,
            "_send_mouse_button",
            lambda button, key_up: clicks.append((button, key_up)),
        )
        backend = self._make_backend()
        backend.click(10, 20, button="right")
        assert moves == [(10, 20)]
        assert clicks == [("right", False), ("right", True)]

    def test_click_unknown_button_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = self._make_backend()
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
