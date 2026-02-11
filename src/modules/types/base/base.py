"""
Provider接口基础数据类定义

定义了新架构中的核心数据结构:
- NormalizedMessage: 标准化消息 (来自 Input Domain)
"""

# NormalizedMessage 定义在 src/modules/types/base/normalized_message.py
from src.modules.types.base.normalized_message import NormalizedMessage

__all__ = ["NormalizedMessage"]
