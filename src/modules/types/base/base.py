"""
Provider接口基础数据类定义

定义了新架构中的核心数据结构:
- NormalizedMessage: 标准化消息 (来自 Input Domain)

注意:
- RawData 已移动到 src/core/base/raw_data.py
- NormalizedMessage 定义在 src/core/base/normalized_message.py
- RenderParameters 定义在 src/domains/output/parameters/render_parameters.py (直接从那里导入)
"""

# 从 data_types 导入 NormalizedMessage
from src.modules.types.base.normalized_message import NormalizedMessage

__all__ = ["NormalizedMessage"]
