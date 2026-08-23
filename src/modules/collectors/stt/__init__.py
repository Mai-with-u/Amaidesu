# 语音转文字采集器（v2 / Wave 5 迁移）
# 迁移自 src/stages/input/collectors/stt/
from .config import (
    AudioConfig,
    IflytekAsrConfig,
    MessageConfig,
    STTInputConfig,
    VadConfig,
)
from .stt_collector import STTCollector

__all__ = [
    "AudioConfig",
    "IflytekAsrConfig",
    "MessageConfig",
    "STTCollector",
    "STTInputConfig",
    "VadConfig",
]
