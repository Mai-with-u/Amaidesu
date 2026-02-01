"""
Input Providers - 输入Provider实现

包含各种 InputProvider 的具体实现：
- ConsoleInputProvider: 控制台输入Provider
- MockDanmakuInputProvider: 模拟弹幕Provider
- BiliDanmakuInputProvider: B站弹幕Provider
- ReadPingmuInputProvider: 屏幕读评输入Provider
- RemoteStreamProvider: 远程流输入Provider
"""

from .console_input_provider import ConsoleInputProvider
from .mock_danmaku_provider import MockDanmakuInputProvider
from .bili_danmaku_provider import BiliDanmakuInputProvider
from .read_pingmu_provider import ReadPingmuInputProvider
from .remote_stream_provider import RemoteStreamProvider

__all__ = [
    "ConsoleInputProvider",
    "MockDanmakuInputProvider",
    "BiliDanmakuInputProvider",
    "ReadPingmuInputProvider",
    "RemoteStreamProvider",
]
