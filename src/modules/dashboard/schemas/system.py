"""
系统状态 Schema

定义系统状态相关的数据模型。
"""

from typing import Optional

from pydantic import BaseModel


class EventStats(BaseModel):
    """事件统计"""

    total_emits: int = 0
    total_subscribers: int = 0


class DomainStatus(BaseModel):
    """域状态"""

    enabled: bool
    active_providers: int = 0
    total_providers: int = 0
    event_stats: Optional[EventStats] = None


class SystemStatusResponse(BaseModel):
    """系统状态响应"""

    running: bool
    uptime_seconds: float
    version: str
    python_version: str
    input_domain: Optional[DomainStatus] = None
    decision_domain: Optional[DomainStatus] = None
    output_domain: Optional[DomainStatus] = None


class SystemStatsResponse(BaseModel):
    """系统统计响应"""

    total_messages: int = 0
    total_intents: int = 0
    event_bus_stats: Optional[EventStats] = None


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = "ok"
    timestamp: float
