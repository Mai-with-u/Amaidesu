"""
OutputPipeline - Output 阶段 Intent 后处理管道系统

OutputPipeline 用于在 Intent 分发给 OutputHandler 前执行过滤/修改/丢弃。
类似于 Input 阶段的 InputPipeline，但处理的是 Intent 对象。

位置（3阶段架构）：
    - Output 阶段: Decider → Intent → OutputPipeline → OUTPUT_INTENT_DISPATCHED 事件 → OutputHandler
    - 调用点: OutputHandlerManager._on_decision_intent()
    - 功能: Intent 过滤、修改、增强等后处理

与 InputPipeline 的区别：
    - InputPipeline: 处理 NormalizedMessage → 返回 NormalizedMessage 或 None
    - OutputPipeline: 处理 Intent → 返回 Intent 或 None
"""

from .base import OutputPipeline, OutputPipelineBase, PipelineStats
from .manager import OutputPipelineManager
from .profanity_filter import ProfanityFilterPipeline
from src.modules.types.base.pipeline_types import PipelineErrorHandling, PipelineException

__all__ = [
    "OutputPipelineBase",
    "OutputPipeline",
    "OutputPipelineManager",
    "ProfanityFilterPipeline",
    "PipelineErrorHandling",
    "PipelineException",
    "PipelineStats",
]
