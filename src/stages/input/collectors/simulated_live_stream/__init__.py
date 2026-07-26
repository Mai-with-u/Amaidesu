"""模拟直播间 Input Collector

提供本地调试用的模拟直播间数据源，可在无真实直播流的情况下
驱动下游 Decision / Output 阶段的联调与回归测试。
"""

from .collector import SimulatedLiveStreamCollector  # noqa: F401

__all__ = [
    "SimulatedLiveStreamCollector",
]
