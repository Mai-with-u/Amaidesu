"""
Provider接口模块

导出了新架构中的所有Provider接口和数据类。
"""

from .base import RawData, RenderParameters, CanonicalMessage
from .input_provider import InputProvider
from .output_provider import OutputProvider
from .decision_provider import DecisionProvider

__all__ = [
    # 数据类
    "RawData",
    "RenderParameters",
    "CanonicalMessage",
    # Provider接口
    "InputProvider",
    "OutputProvider",
    "DecisionProvider",
]
