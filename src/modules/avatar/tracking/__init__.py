"""面捕合成器（tracking）：替代摄像头的面部捕捉数据来源

AI 主播背后没有真人做面捕，这里合成一个：每秒若干帧算出"此刻脸和身体该是什么
样"（平台无关的通用通道），交给各平台驱动写成本平台的输入。快的身体过程（眨眼、
眼神、头部漫步、表情起落）在这里按规则生成，主播 Agent 只发事实事件。
"""

from .channels import CHANNELS, Frame, rest_frame
from .config import TrackingConfig
from .runtime import CalibratableDriver, TrackingDriver, TrackingRuntime
from .synthesizer import TrackingSynthesizer

__all__ = [
    "CHANNELS",
    "CalibratableDriver",
    "Frame",
    "TrackingConfig",
    "TrackingDriver",
    "TrackingRuntime",
    "TrackingSynthesizer",
    "rest_frame",
]
