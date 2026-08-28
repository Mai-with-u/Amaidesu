"""
系统状态 Schema

定义系统状态相关的数据模型（v2 适配 Wave U1 / B1-B2）。

v2 变化：
- 移除 PhaseStatus（旧 input/decision/output 阶段字段，主线从未注入 v1 manager）
- 引入 GroupStatus 表达采集器/Agent/工具三组的启用/运行/总数计数
- 引入 EventBusStats 表达 EventBus 总吞吐量
- 移除 SystemStatsResponse（与 /system/stats 端点一并删除）
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
