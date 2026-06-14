"""
基础数据类型定义

定义了核心数据结构和类型。
"""

from .base import NormalizedMessage
from .pipeline_types import PipelineErrorHandling, PipelineException

__all__ = [
    "NormalizedMessage",
    "PipelineErrorHandling",
    "PipelineException",
]
