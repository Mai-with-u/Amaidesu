"""
Input Providers - 输入Provider实现

包含各种 InputProvider 的具体实现：
- ConsoleInputProvider: 控制台输入Provider
- MockDanmakuInputProvider: 模拟弹幕Provider
- BiliDanmakuInputProvider: B站弹幕Provider
- BiliDanmakuOfficialInputProvider: B站官方弹幕Provider
- BiliDanmakuOfficialMaiCraftInputProvider: B站官方弹幕+Minecraft转发Provider
- ReadPingmuInputProvider: 屏幕读评输入Provider
- MainosabaInputProvider: 游戏画面文本采集Provider
- STTInputProvider: 语音转文字输入Provider

注意：RemoteStreamProvider 已移动到 src/domains/output/providers/remote_stream/
作为 RemoteStreamOutputProvider，因为它是一个输出Provider。
"""

from .bili_danmaku import BiliDanmakuInputProvider
from .bili_danmaku_official import BiliDanmakuOfficialInputProvider
from .bili_danmaku_official_maicraft import BiliDanmakuOfficialMaiCraftInputProvider
from .console_input import ConsoleInputProvider
from .mainosaba import MainosabaInputProvider
from .mock_danmaku import MockDanmakuInputProvider
from .read_pingmu import ReadPingmuInputProvider
from .stt import STTInputProvider

__all__ = [
    "ConsoleInputProvider",
    "MockDanmakuInputProvider",
    "BiliDanmakuInputProvider",
    "BiliDanmakuOfficialInputProvider",
    "BiliDanmakuOfficialMaiCraftInputProvider",
    "ReadPingmuInputProvider",
    "MainosabaInputProvider",
    "STTInputProvider",
]
