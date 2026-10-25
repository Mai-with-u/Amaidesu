"""键鼠注入后端——text_adv 动作出口的触达通道。

定位：
- 按键/点击是**被动能力**（被调才干活），由 :class:`InputBackend` Protocol
  统一注入；生产实现为 :class:`Win32InputBackend`（ctypes 直调 Windows
  user32 的 SendInput/SetCursorPos，Windows 专属），测试用
  :class:`FakeInputBackend`（只记录调用序列，不触碰真实键鼠）。
- ``hold`` 是**按住语义**（keyDown → 等待 → keyUp）：跳过等能力依赖
  "持续按住"，单次 press 无法表达。

失败语义：
- 注入动作在正常使用下不产生可恢复失败；异常由上层动作编排捕获并分流，
  本模块不静默吞异常。
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Protocol

# ======================================================================
# Win32 常量与结构（ctypes 直调 SendInput 所需）
# ======================================================================

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_VK_PACKET = 0xE7  # KEYEVENTF_UNICODE 下 wVk 固定取此值，真实字符走 wScan
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040

# 按钮 → (down 事件 flag, up 事件 flag)
_MOUSE_BUTTON_FLAGS: dict[str, tuple[int, int]] = {
    "left": (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP),
    "right": (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP),
    "middle": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
}


class _KEYBDINPUT(ctypes.Structure):
    """Win32 KEYBDINPUT：SendInput 的键盘事件负载。"""

    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MOUSEINPUT(ctypes.Structure):
    """Win32 MOUSEINPUT：SendInput 的鼠标事件负载。"""

    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT(ctypes.Structure):
    """Win32 INPUT：SendInput 的事件信封，type 决定 union 活跃成员。"""

    class _U(ctypes.Union):
        _fields_ = [
            ("mi", _MOUSEINPUT),
            ("ki", _KEYBDINPUT),
        ]

    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("u", _U),
    ]


# ======================================================================
# 键名 → 虚拟键码映射（覆盖常见键名全集：字母数字、功能键、修饰键、导航键等；表驱动纯函数）
# ======================================================================

_NAME_TO_VK: dict[str, int] = {
    # 字母与数字（键名一律小写）
    **{chr(ord("a") + i): 0x41 + i for i in range(26)},
    **{str(d): 0x30 + d for d in range(10)},
    # 功能键 F1–F24
    **{f"f{n}": 0x70 + n - 1 for n in range(1, 25)},
    # 修饰键（左右不分的常用名映射到左键）
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "win": 0x5B,
    "shiftleft": 0xA0,
    "shiftright": 0xA1,
    "ctrlleft": 0xA2,
    "ctrlright": 0xA3,
    "altleft": 0xA4,
    "altright": 0xA5,
    "winleft": 0x5B,
    "winright": 0x5C,
    # 方向键与导航键
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "tab": 0x09,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "ins": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "printscreen": 0x2C,
    "prtsc": 0x2C,
    "capslock": 0x14,
    "numlock": 0x90,
    "scrolllock": 0x91,
    "pause": 0x13,
    # 数字小键盘
    **{f"num{n}": 0x60 + n for n in range(10)},
    "nummultiply": 0x6A,
    "numadd": 0x6B,
    "numsubtract": 0x6D,
    "numdecimal": 0x6E,
    "numdivide": 0x6F,
}


def _key_to_vk(key: str) -> int | None:
    """键名查虚拟键码；不在映射表时返回 None（交由调用方按单字符/错误处理）。"""
    return _NAME_TO_VK.get(key)


# ======================================================================
# user32 薄封装（模块级引用，测试可 monkeypatch，headless 不真调注入 API）
# ======================================================================

_user32: ctypes.WinDLL | None = None


def _get_user32() -> ctypes.WinDLL:
    """懒加载 user32：Windows 专属，非 Windows 访问 ctypes.windll 即失败。"""
    global _user32
    if _user32 is None:
        _user32 = ctypes.windll.user32
    return _user32


def _send_key(vk: int, scan: int, key_up: bool) -> None:
    """发送一次键盘事件（扫描码由 MapVirtualKeyW 求得，与 keybd_event 行为对齐）。"""
    flags = _KEYEVENTF_KEYUP if key_up else 0
    event = _INPUT(type=_INPUT_KEYBOARD)
    event.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    _get_user32().SendInput(1, ctypes.byref(event), ctypes.sizeof(event))


def _send_unicode_char(ch: str) -> None:
    """以 KEYEVENTF_UNICODE 发送单个字符（wScan 携带 UTF-16 码元）。"""
    code = ord(ch)
    for key_up in (False, True):
        flags = _KEYEVENTF_UNICODE
        if key_up:
            flags |= _KEYEVENTF_KEYUP
        event = _INPUT(type=_INPUT_KEYBOARD)
        event.ki = _KEYBDINPUT(wVk=_VK_PACKET, wScan=code, dwFlags=flags, time=0, dwExtraInfo=0)
        _get_user32().SendInput(1, ctypes.byref(event), ctypes.sizeof(event))


def _move_cursor(x: int, y: int) -> None:
    """移动光标到主显示器物理像素坐标。"""
    _get_user32().SetCursorPos(x, y)


def _send_mouse_button(button: str, key_up: bool) -> None:
    """发送一次鼠标按键事件。"""
    flags = _MOUSE_BUTTON_FLAGS[button][1 if key_up else 0]
    event = _INPUT(type=_INPUT_MOUSE)
    event.mi = _MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0)
    _get_user32().SendInput(1, ctypes.byref(event), ctypes.sizeof(event))


class InputBackend(Protocol):
    """键鼠注入后端协议（依赖注入点）。

    生产实现为 :class:`Win32InputBackend`；测试用
    :class:`FakeInputBackend`。
    """

    def press(self, key: str) -> None:
        """单次按键。"""
        ...

    def hold(self, key: str, duration_ms: int) -> None:
        """按住 ``key`` 持续 ``duration_ms`` 毫秒后松开（按住语义）。"""
        ...

    def click(self, x: int, y: int, button: str = "left") -> None:
        """在屏幕坐标 ``(x, y)`` 处点击（显示器相对物理像素）。"""
        ...


class Win32InputBackend:
    """基于 ctypes 直调 Win32 API 的键鼠注入实现（Windows 专属）。

    按键走 SendInput（虚拟键码 + 扫描码，与 keybd_event 行为一致），
    点击走 SetCursorPos + SendInput 鼠标事件；坐标为主显示器物理像素。

    构造即调 ``SetProcessDPIAware()``：坐标语义依赖进程 DPI 感知——
    未感知时 GetCursorPos/SetCursorPos 的坐标系会被虚拟化缩放，与读屏
    截图得到物理像素坐标不一致；进程已被设置过时该调用失败属正常，忽略。

    非 Windows 环境（CI、headless 机器）构造即抛错，不让模块级 import
    承担平台差异；生产（Windows 直播机）实例化时环境必然齐备。
    """

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError(
                "Win32InputBackend 仅支持 Windows（依赖 user32 键鼠注入 API），非 Windows 环境请改用 FakeInputBackend"
            )
        self._user32 = _get_user32()
        # 返回值刻意忽略：进程 DPI 感知已被其他组件设置过时调用失败属正常
        self._user32.SetProcessDPIAware()

    def press(self, key: str) -> None:
        vk = _key_to_vk(key)
        if vk is not None:
            scan = self._user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
            _send_key(vk, scan, key_up=False)
            _send_key(vk, scan, key_up=True)
        elif len(key) == 1:
            _send_unicode_char(key)
        else:
            raise ValueError(
                f"不支持的键名 {key!r}：映射表外的多字符键名无法注入，"
                "请使用单字符或标准键名（如 'space'、'enter'、'f5'）"
            )

    def hold(self, key: str, duration_ms: int) -> None:
        vk = _key_to_vk(key)
        if vk is None:
            raise ValueError(f"不支持的键名 {key!r}：按住仅支持映射表内的标准键名（如 'ctrl'、'shift'、'space'）")
        scan = self._user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
        _send_key(vk, scan, key_up=False)
        try:
            time.sleep(duration_ms / 1000.0)
        finally:
            _send_key(vk, scan, key_up=True)

    def click(self, x: int, y: int, button: str = "left") -> None:
        if button not in _MOUSE_BUTTON_FLAGS:
            raise ValueError(f"不支持的鼠标按钮 {button!r}，可选：left / right / middle")
        _move_cursor(x, y)
        _send_mouse_button(button, key_up=False)
        _send_mouse_button(button, key_up=True)


class FakeInputBackend:
    """测试用键鼠后端：不触碰真实键鼠，只记录调用序列供断言。

    ``hold`` 记录为 ``keyDown:<key>`` / ``keyUp:<key>`` 两条，保序；
    ``press`` 记录为 ``press:<key>``；``click`` 记录为
    ``click:<x>,<y>,<button>``。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def press(self, key: str) -> None:
        self.calls.append(f"press:{key}")

    def hold(self, key: str, duration_ms: int) -> None:
        self.calls.append(f"keyDown:{key}")
        self.calls.append(f"keyUp:{key}")

    def click(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(f"click:{x},{y},{button}")
