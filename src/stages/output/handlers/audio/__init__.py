"""
语音输出模块（Audio Handler 集合）

包含所有 TTS/语音相关的 Handler：
- AudioHandlerBase: 语音合成 Handler 抽象基类
- EdgeTTSHandler: Edge TTS 语音输出
- GPTSoVITSHandler: GPT-SoVITS 语音输出
- OmniTTSHandler: Omni TTS 语音输出

共享工具位于 audio/utils/：
- device_finder: 音频输出设备查找
- wav_decoder: WAV 文件解码
"""

from .base import AudioHandlerBase

# 导入子模块以触发 @handler 装饰器注册
from . import (  # noqa: F401
    edge_tts,
    gptsovits,
    omni_tts,
)

from .edge_tts import EdgeTTSHandler
from .gptsovits import GPTSoVITSHandler
from .omni_tts import OmniTTSHandler

__all__ = [
    "AudioHandlerBase",
    "EdgeTTSHandler",
    "GPTSoVITSHandler",
    "OmniTTSHandler",
]
