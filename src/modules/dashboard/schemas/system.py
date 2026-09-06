"""
系统状态 Schema

定义系统状态相关的数据模型。
"""

from pydantic import BaseModel


class GroupStatus(BaseModel):
    """组件组状态（采集器 / Agent / 工具）。"""

    enabled: int = 0
    started: int = 0
    total: int = 0


class EventBusStats(BaseModel):
    """EventBus 统计信息（聚合视图）。"""

    total_events: int = 0


class SystemStatusResponse(BaseModel):
    """系统状态响应（v2：采集器/Agent/工具三组 + EventBus 总吞吐）。"""

    running: bool
    uptime_seconds: float
    version: str
    python_version: str
    groups: dict[str, GroupStatus]
    event_bus: EventBusStats


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = "ok"
    timestamp: float
