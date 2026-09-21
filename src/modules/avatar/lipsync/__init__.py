"""口型共享件（lipsync）——分析器 + 平台渲染器。

分析器是共享基础设施（一个实例扇出多具皮套），调参在 infra.toml
``[avatar.lipsync]``；平台渲染器住各平台适配器（VTS / Warudo），把平台
无关的 ``MouthSignal`` 翻译为本平台参数。
"""

from .analyzer import LipSyncAnalyzer, LipSyncConfig, LipSyncRenderer, MouthSignal

__all__ = ["LipSyncAnalyzer", "LipSyncConfig", "LipSyncRenderer", "MouthSignal"]
