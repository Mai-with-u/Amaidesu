"""
Provider抽象基类

定义了三种核心Provider接口：
- InputProvider: 输入Provider抽象基类
- DecisionProvider: 决策Provider抽象基类
- OutputProvider: 输出Provider抽象基类

以及核心数据类型：
- NormalizedMessage: 标准化消息

注意:
"""

from .base import NormalizedMessage
from .decision_provider import DecisionProvider
from .input_provider import InputProvider
from .output_provider import OutputProvider

__all__ = [
    "InputProvider",
    "DecisionProvider",
    "OutputProvider",
    "NormalizedMessage",
]
