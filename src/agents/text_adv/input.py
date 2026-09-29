"""键鼠注入后端——text_adv 动作出口的触达通道。

定位：
- 按键/点击是**被动能力**（被调才干活），由 :class:`InputBackend` Protocol
  统一注入；生产实现为 ``PyAutoGuiInputBackend``，测试用
  :class:`FakeInputBackend`（只记录调用序列，不触碰真实键鼠）。
- ``hold`` 是**按住语义**（keyDown → 等待 → keyUp）：跳过等能力依赖
  "持续按住"，单次 press 无法表达。

失败语义：
- pyautogui 的注入动作在正常使用下不产生可恢复失败；异常由上层动作
  编排捕获并分流，本模块不静默吞异常。
"""

from __future__ import annotations

import time
from typing import Protocol


class InputBackend(Protocol):
    """键鼠注入后端协议（依赖注入点）。

    生产实现为 :class:`PyAutoGuiInputBackend`；测试用
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


class PyAutoGuiInputBackend:
    """基于 pyautogui 的键鼠注入实现。

    初始化即关闭 FAILSAFE（直播中鼠标被甩到屏幕角落会触发 pyautogui
    的紧急停止异常，直接打断 Agent）并清零全局 PAUSE（逐次调用间的
    内置停顿会拖慢推进节奏）。

    pyautogui 延迟到实例化才导入：其依赖链在无显示环境（CI、headless
    机器）import 期即失败，模块级 import 会让本包整体不可导入；生产
    （Windows 直播机）实例化时环境必然齐备。
    """

    def __init__(self) -> None:
        import pyautogui

        self._pyautogui = pyautogui
        self._pyautogui.FAILSAFE = False
        self._pyautogui.PAUSE = 0

    def press(self, key: str) -> None:
        self._pyautogui.press(key)

    def hold(self, key: str, duration_ms: int) -> None:
        self._pyautogui.keyDown(key)
        try:
            time.sleep(duration_ms / 1000.0)
        finally:
            self._pyautogui.keyUp(key)

    def click(self, x: int, y: int, button: str = "left") -> None:
        self._pyautogui.click(x=x, y=y, button=button)


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
