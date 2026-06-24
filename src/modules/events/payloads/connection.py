"""
通用组件事件 Payload 定义
"""

import time
from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event


@register_event("connection.event")
class ConnectionEventPayload(BasePayload):
    """通用组件事件 Payload"""

    name: str = Field(..., description="组件名称")
    layer: str = Field(..., description="组件层级 (input/output)")
    reason: Optional[str] = Field(default=None, description="断开原因")
    will_retry: bool = Field(default=False, description="是否将重试")
    timestamp_ms: int = Field(
        default_factory=lambda: int(time.time() * 1000),
        alias="timestamp",
        description="事件时间戳（Unix 毫秒）",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "name": "bili_danmaku",
                "layer": "input",
                "reason": "connection_lost",
                "will_retry": True,
                "timestamp_ms": 1706745600000,
                "metadata": {"reconnect_attempt": 1},
            }
        },
    )

    def __str__(self) -> str:
        return f'{self.__class__.__name__}(name="{self.name}", layer="{self.layer}")'
