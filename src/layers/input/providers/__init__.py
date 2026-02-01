"""
Input Providers - 输入Provider实现

包含各种 InputProvider 的具体实现：
- ConsoleInputProvider: 控制台输入Provider
- MockDanmakuProvider: 模拟弹幕Provider
"""

from .console_input_provider import ConsoleInputProvider
from .mock_danmaku_provider import MockDanmakuProvider

__all__ = ["ConsoleInputProvider", "MockDanmakuProvider"]
