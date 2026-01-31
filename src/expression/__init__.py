"""
Expression生成层 - Layer 5

职责:
- 将Intent转换为RenderParameters
- 映射情感到表情参数
- 映射动作到渲染指令

核心组件:
- RenderParameters: 渲染参数数据类
- ExpressionGenerator: 表达式生成器
- EmotionMapper: 情感映射器
- ActionMapper: 动作映射器
"""

from .render_parameters import RenderParameters
from .expression_generator import ExpressionGenerator
from .emotion_mapper import EmotionMapper
from .action_mapper import ActionMapper
from .expression_mapper import ExpressionMapper

__all__ = ["RenderParameters", "ExpressionGenerator", "EmotionMapper", "ActionMapper", "ExpressionMapper"]
