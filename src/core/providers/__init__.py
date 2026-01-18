"""
Provider接口模块

导出了新架构中的所有Provider接口和数据类。
"""

from .base import RenderParameters, CanonicalMessage
from .input_provider import InputProvider
from .output_provider import OutputProvider
from .decision_provider import DecisionProvider

# RawData 和 NormalizedText 从 data_types 导入，避免重复定义
from src.core.data_types.raw_data import RawData
from src.core.data_types.normalized_text import NormalizedText

__all__ = [
    # 数据类（从data_types重新导出）
    "RawData",
    "NormalizedText",
    "RenderParameters",
    "CanonicalMessage",
    # Provider接口
    "InputProvider",
    "OutputProvider",
    "DecisionProvider",
]
