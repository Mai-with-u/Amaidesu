"""Pipeline 配置 Schema。

使用 Pydantic BaseModel 校验 Pipeline 配置，配置错误立即抛出而非静默跳过。
"""

from __future__ import annotations

from typing import Dict, Literal

from pydantic import BaseModel, ConfigDict, Field


class PipelineItemConfig(BaseModel):
    """单个 Pipeline 的配置。

    Attributes:
        priority: 优先级，数值越小越先执行（默认 500）
        enabled: 是否启用（默认 True）
        error_handling: 错误处理策略（默认 continue）
        timeout_seconds: 单次处理超时时间（默认 5.0 秒）
    """

    model_config = ConfigDict(extra="allow")

    priority: int = 500
    enabled: bool = True
    error_handling: Literal["continue", "stop", "drop"] = "continue"
    timeout_seconds: float = Field(default=5.0, gt=0.0)


class PipelinesRootConfig(BaseModel):
    """根 [pipelines] 配置。

    Attributes:
        input: Input 阶段 Pipeline 配置（key 为 pipeline name）
        output: Output 阶段 Pipeline 配置（key 为 pipeline name）
    """

    input: Dict[str, PipelineItemConfig] = Field(default_factory=dict)
    output: Dict[str, PipelineItemConfig] = Field(default_factory=dict)


__all__ = ["PipelineItemConfig", "PipelinesRootConfig"]
