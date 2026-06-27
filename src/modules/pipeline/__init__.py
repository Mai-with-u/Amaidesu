"""Pipeline 共享类型与基础设施。

提供：
- Pipeline[T] 泛型基类
- PipelineManager[T] 泛型管理器
- @pipeline 装饰器与注册表
- PipelineItemConfig Schema
"""

from src.modules.pipeline.base import Pipeline
from src.modules.pipeline.config import PipelineItemConfig, PipelinesRootConfig
from src.modules.pipeline.manager import PipelineManager
from src.modules.pipeline.registry import (
    PIPELINE_REGISTRY,
    PipelineRegistrationError,
    InputPipeline,
    OutputPipeline,
    clear_registry,
    pipeline,
)

__all__ = [
    "Pipeline",
    "PipelineManager",
    "PipelineItemConfig",
    "PipelinesRootConfig",
    "PIPELINE_REGISTRY",
    "PipelineRegistrationError",
    "InputPipeline",
    "OutputPipeline",
    "clear_registry",
    "pipeline",
]
