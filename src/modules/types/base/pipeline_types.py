"""
Pipeline 共享类型定义

供 InputPipeline 和 OutputPipeline 共同使用的错误处理策略和异常类。
"""

from enum import Enum
from typing import Optional


class PipelineErrorHandling(str, Enum):
    """Pipeline 错误处理策略"""

    CONTINUE = "continue"  # 记录日志，继续执行下一个 Pipeline
    STOP = "stop"  # 停止执行，抛出异常
    DROP = "drop"  # 丢弃消息，不执行后续 Pipeline


class PipelineException(Exception):
    """Pipeline 处理异常"""

    def __init__(
        self,
        pipeline_name: str,
        message: str,
        original_error: Optional[Exception] = None,
    ):
        self.pipeline_name = pipeline_name
        self.message = message
        self.original_error = original_error
        super().__init__(f"[{pipeline_name}] {message}")
        # 设置异常链，使 __cause__ 可用
        if original_error is not None:
            self.__cause__ = original_error
