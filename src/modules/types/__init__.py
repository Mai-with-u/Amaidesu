"""核心类型定义。

跨阶段共享的类型定义,避免循环依赖。
"""

from .intent import (
    Intent,
    IntentAction,
    IntentEmotion,
    IntentMetadata,
)

__all__ = [
    "Intent",
    "IntentAction",
    "IntentEmotion",
    "IntentMetadata",
]
