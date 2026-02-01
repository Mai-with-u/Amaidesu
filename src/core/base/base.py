"""
Provider接口基础数据类定义

定义了新架构中的核心数据结构:
- NormalizedMessage: 标准化消息(来自Layer 2)
- RenderParameters: 渲染参数(传递给Layer 5: 渲染呈现层)

注意:
- RawData 已移动到 src/data_types/
- NormalizedMessage 定义在 src/data_types/normalized_message.py
- RenderParameters 定义在 src/layers/parameters/render_parameters.py
"""

# 从 data_types 导入 NormalizedMessage
from src.data_types.normalized_message import NormalizedMessage

# 从 parameters 导入 RenderParameters
from src.layers.parameters.render_parameters import RenderParameters

__all__ = ["RenderParameters", "NormalizedMessage"]
