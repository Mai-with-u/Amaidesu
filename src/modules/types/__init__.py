"""核心类型定义

跨域共享的类型定义，避免循环依赖。
"""

from .intent import (
    ActionType,
    EmotionType,
    Intent,
    IntentAction,
    SourceContext,
)
from .render_parameters import ExpressionParameters

# 向后兼容别名（保留以支持现有代码）
RenderParameters = ExpressionParameters

__all__ = [
    "EmotionType",
    "ActionType",
    "IntentAction",
    "SourceContext",
    "Intent",
    "ExpressionParameters",
    "RenderParameters",
]
