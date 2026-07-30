"""
核心系统事件 Payload 定义

定义 core.* 事件（core.startup / core.shutdown / core.error）的 Payload 类型。
这些事件由 EventBus 内部发射，供 EventRecorder、EventBroadcaster 等系统组件订阅。
"""

from typing import Any, Optional

from pydantic import Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event


@register_event("core.startup")
class CoreStartupPayload(BasePayload):
    """系统启动事件 Payload"""

    event: Optional[str] = Field(default=None, description="事件名称")
    message: Optional[str] = Field(default=None, description="事件消息")
    data: Any = Field(default=None, description="附加数据")


@register_event("core.shutdown")
class CoreShutdownPayload(BasePayload):
    """系统关闭事件 Payload"""

    event: Optional[str] = Field(default=None, description="事件名称")
    message: Optional[str] = Field(default=None, description="事件消息")
    data: Any = Field(default=None, description="附加数据")


@register_event("core.error")
class CoreErrorPayload(BasePayload):
    """系统错误事件 Payload"""

    event: Optional[str] = Field(default=None, description="事件名称")
    message: Optional[str] = Field(default=None, description="事件消息")
    data: Any = Field(default=None, description="附加数据")
