"""
Expression生成层 - Output Domain: 参数生成

职责:
- RenderParameters: 渲染参数数据类
- ActionMapper: 动作映射器

注意: ExpressionGenerator、EmotionMapper、ExpressionMapper 已移除
- Avatar Provider 现在直接订阅 DECISION_INTENT_GENERATED 事件
- 每个Provider内部实现自己的Intent到平台参数的适配逻辑
"""

from .action_mapper import ActionMapper
from .render_parameters import RenderParameters

__all__ = ["RenderParameters", "ActionMapper"]
