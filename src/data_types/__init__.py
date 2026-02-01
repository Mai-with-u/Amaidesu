"""
Phase 2: 输入层数据类型

包含Layer 1(输入感知层)和Layer 2(输入标准化层)的数据结构定义。
"""

from .raw_data import RawData
from .normalized_text import NormalizedText

__all__ = ["RawData", "NormalizedText"]
