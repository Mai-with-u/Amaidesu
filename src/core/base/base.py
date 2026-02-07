"""
Provider接口基础数据类定义

定义了新架构中的核心数据结构:
- NormalizedMessage: 标准化消息 (来自 Input Domain)
- RenderParameters: 渲染参数 (传递给 Output Domain: 渲染输出)

注意:
- RawData 已移动到 src/core/base/raw_data.py
- NormalizedMessage 定义在 src/core/base/normalized_message.py
- RenderParameters 定义在 src/domains/output/parameters/render_parameters.py
"""

# 从 data_types 导入 NormalizedMessage
from src.core.base.normalized_message import NormalizedMessage

# 从 parameters 导入 RenderParameters
from src.domains.output.parameters.render_parameters import RenderParameters

__all__ = ["RenderParameters", "NormalizedMessage"]
