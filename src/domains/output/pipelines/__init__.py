"""
OutputPipeline - Output Domain 参数后处理管道系统

OutputPipeline 用于在 ExpressionParameters 生成后、发布事件前对参数进行后处理。
类似于 Input Domain 的 TextPipeline，但处理的是 ExpressionParameters 对象而非文本。

位置（3域架构）：
    - OutputDomain: ExpressionGenerator → FlowCoordinator → EventBus → OutputProvider
    - 调用点: FlowCoordinator._on_intent_ready()
    - 功能: 参数过滤、修改、增强等后处理

与 TextPipeline 的区别：
    - TextPipeline: 处理文本 (str) → 返回文本或 None
    - OutputPipeline: 处理参数 (ExpressionParameters) → 返回参数或 None
"""

from .base import OutputPipeline, OutputPipelineBase, PipelineErrorHandling, PipelineException, PipelineStats
from .manager import OutputPipelineManager
from .profanity_filter import ProfanityFilterOutputPipeline

__all__ = [
    "OutputPipelineBase",
    "OutputPipeline",
    "OutputPipelineManager",
    "ProfanityFilterOutputPipeline",
    "PipelineErrorHandling",
    "PipelineException",
    "PipelineStats",
]
