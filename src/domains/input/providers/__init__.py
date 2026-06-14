"""
Input Collectors - 输入 Collector 实现

包含各种 InputCollector 的具体实现：
- ConsoleInputCollector: 控制台输入Collector
- MockDanmakuCollector: 模拟弹幕Collector
- BiliDanmakuCollector: B站弹幕Collector
- BiliDanmakuOfficialCollector: B站官方弹幕Collector
- BiliDanmakuOfficialMaiCraftCollector: B站官方弹幕+Minecraft转发Collector
- ReadPingmuCollector: 屏幕读评输入Collector
- MainosabaCollector: 游戏画面文本采集Collector
- STTCollector: 语音转文字输入Collector

注意：RemoteStreamProvider 已移动到 src/domains/output/providers/remote_stream/
作为 RemoteStreamHandler，因为它是一个输出Provider。

收集器通过 @collector 装饰器自动注册到 registry.py 中的 _COLLECTORS 字典。
导入此包会触发所有收集器的注册。
"""

from .bili_danmaku import BiliDanmakuCollector
from .bili_danmaku_official import BiliDanmakuOfficialCollector
from .bili_danmaku_official_maicraft import BiliDanmakuOfficialMaiCraftCollector
from .console_input import ConsoleInputCollector
from .mainosaba import MainosabaCollector
from .mock_danmaku import MockDanmakuCollector
from .read_pingmu import ReadPingmuCollector
from .stt import STTCollector

__all__ = [
    "ConsoleInputCollector",
    "MockDanmakuCollector",
    "BiliDanmakuCollector",
    "BiliDanmakuOfficialCollector",
    "BiliDanmakuOfficialMaiCraftCollector",
    "ReadPingmuCollector",
    "MainosabaCollector",
    "STTCollector",
]
