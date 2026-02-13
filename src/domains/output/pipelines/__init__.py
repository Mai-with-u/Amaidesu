"""
OutputPipeline - Output Domain Intent 后处理管道系统

OutputPipeline 用于在 Intent 分发给 OutputProvider 前执行过滤/修改/丢弃。
类似于 Input Domain 的 TextPipeline，但处理的是 Intent 对象。

位置（3域架构）：
    - OutputDomain: DecisionProvider → Intent → OutputPipeline → OUTPUT_INTENT 事件 → OutputProvider
    - 调用点: OutputProviderManager._on_decision_intent()
    - 功能: Intent 过滤、修改、增强等后处理

与 TextPipeline 的区别：
    - TextPipeline: 处理 NormalizedMessage → 返回 NormalizedMessage 或 None
    - OutputPipeline: 处理 Intent → 返回 Intent 或 None
"""

from .base import OutputPipeline, OutputPipelineBase, PipelineErrorHandling, PipelineException, PipelineStats
from .manager import OutputPipelineManager
from .profanity_filter import ProfanityFilterPipeline

__all__ = [
    "OutputPipelineBase",
    "OutputPipeline",
    "OutputPipelineManager",
    "ProfanityFilterPipeline",
    "PipelineErrorHandling",
    "PipelineException",
    "PipelineStats",
]
