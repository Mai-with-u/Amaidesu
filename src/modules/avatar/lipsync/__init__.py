"""口型共享件（lipsync）——分析器 + 平台渲染器。

分析器是共享基础设施（一个实例扇出多具虚拟形象），调参在 infra.toml
``[avatar.lipsync]``；渲染器有两类：面捕合成器只取嘴张开量合成进每一帧（VTS），
尚未移植的平台适配器（Warudo）把 ``MouthSignal`` 翻译为本平台参数。
"""

from .analyzer import LipSyncAnalyzer, LipSyncConfig, LipSyncRenderer, MouthSignal

__all__ = ["LipSyncAnalyzer", "LipSyncConfig", "LipSyncRenderer", "MouthSignal"]
