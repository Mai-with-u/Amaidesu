"""
Input Providers - 输入Provider实现

包含各种 InputProvider 的具体实现：
- ConsoleInputProvider: 控制台输入Provider
- MockDanmakuInputProvider: 模拟弹幕Provider
- BiliDanmakuInputProvider: B站弹幕Provider
- BiliDanmakuOfficialInputProvider: B站官方弹幕Provider
- BiliDanmakuOfficialMaiCraftInputProvider: B站官方弹幕+Minecraft转发Provider
- ReadPingmuInputProvider: 屏幕读评输入Provider
- RemoteStreamProvider: 远程流输入Provider
"""

from .console_input import ConsoleInputProvider
from .mock_danmaku import MockDanmakuInputProvider
from .bili_danmaku import BiliDanmakuInputProvider
from .bili_official import BiliDanmakuOfficialInputProvider
from .bili_official_maicraft import BiliDanmakuOfficialMaiCraftInputProvider
from .read_pingmu import ReadPingmuInputProvider
from .remote_stream import RemoteStreamProvider

__all__ = [
    "ConsoleInputProvider",
    "MockDanmakuInputProvider",
    "BiliDanmakuInputProvider",
    "BiliDanmakuOfficialInputProvider",
    "BiliDanmakuOfficialMaiCraftInputProvider",
    "ReadPingmuInputProvider",
    "RemoteStreamProvider",
]
