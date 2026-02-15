"""
通用 Provider 事件 Payload 定义
"""

import time
from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload


class GenericProviderPayload(BasePayload):
    """通用 Provider 事件 Payload"""

    provider: str = Field(..., description="Provider 名称")
    layer: str = Field(..., description="Provider 层级 (input/output)")
    reason: Optional[str] = Field(default=None, description="断开原因")
    will_retry: bool = Field(default=False, description="是否将重试")
    timestamp: float = Field(default_factory=time.time, description="事件时间戳")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider": "bili_danmaku",
                "layer": "input",
                "reason": "connection_lost",
                "will_retry": True,
                "timestamp": 1706745600.0,
                "metadata": {"reconnect_attempt": 1},
            }
        }
    )

    def __str__(self) -> str:
        return f'{self.__class__.__name__}(provider="{self.provider}", layer="{self.layer}")'
