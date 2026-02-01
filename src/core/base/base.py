"""
Provider接口基础数据类定义

定义了新架构中的核心数据结构:
- RenderParameters: 渲染参数(传递给Layer 7: 渲染呈现层)
- CanonicalMessage: 标准消息(来自Layer 3)

注意:
- RawData 已移动到 src/data_types/
- ExpressionParameters 和 RenderParameters 定义在 src/layers/expression/render_parameters.py
- CanonicalMessage 定义在 src/layers/canonical/canonical_message.py
"""

# 从 canonical 导入 CanonicalMessage
from src.layers.canonical.canonical_message import CanonicalMessage

# 从 expression 导入 RenderParameters (ExpressionParameters 的别名)
from src.layers.expression.render_parameters import RenderParameters

__all__ = ["RenderParameters", "CanonicalMessage"]
