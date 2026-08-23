# 屏幕变化采集器（v2 / Wave 5 迁移）
# 迁移自 src/stages/input/collectors/read_pingmu/（改名去拼音：read_pingmu → screen）
# 类名：ReadPingmuCollector → ScreenChangeCollector
# ScreenAnalyzer（差异检测）+ ScreenReader（VLM 分析 + 缓存去重）verbatim。
from .screen_analyzer import ScreenAnalyzer
from .screen_change_collector import ScreenChangeCollector
from .screen_reader import ScreenReader

__all__ = [
    "ScreenAnalyzer",
    "ScreenChangeCollector",
    "ScreenReader",
]
