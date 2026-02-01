"""
Perception Text - 文本输入Provider

包含控制台输入和模拟弹幕Provider。
"""

from .console_input_provider import ConsoleInputProvider
from .mock_danmaku_provider import MockDanmakuProvider

__all__ = ["ConsoleInputProvider", "MockDanmakuProvider"]
