"""Backpressure strategies and subscriber configuration for audio streaming."""

from enum import Enum
from typing import Dict

from pydantic import BaseModel, Field


class BackpressureStrategy(str, Enum):
    """音频流背压策略"""

    BLOCK = "block"  # 阻塞发布者（等待订阅者）
    DROP_NEWEST = "drop_newest"  # 丢弃新数据（保护旧数据）
    DROP_OLDEST = "drop_oldest"  # 丢弃最旧数据（滑动窗口）
    FAIL_FAST = "fail_fast"  # 队列满时立即抛出异常


class SubscriberConfig(BaseModel):
    """订阅者配置"""

    queue_size: int = Field(default=100, ge=1, le=1000, description="队列大小")
    backpressure_strategy: BackpressureStrategy = Field(
        default=BackpressureStrategy.DROP_NEWEST, description="背压策略"
    )
    degradation_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="降级阈值（丢弃率超过此值时降级）")


class PublishResult(BaseModel):
    """音频发布结果"""

    success_count: int = Field(default=0, description="成功发布的订阅者数量")
    drop_count: int = Field(default=0, description="丢弃的订阅者数量")
    errors: Dict[str, str] = Field(default_factory=dict, description="错误信息")
