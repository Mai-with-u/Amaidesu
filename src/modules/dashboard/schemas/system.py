"""
系统状态 Schema

定义系统状态相关的数据模型。
"""

from typing import Dict, List, Literal, Optional

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
    uptime_ms: int
    version: str
    python_version: str
    groups: dict[str, GroupStatus]
    event_bus: EventBusStats


DomainHealthState = Literal["ok", "degraded", "offline", "unknown"]


class DomainHealthItem(BaseModel):
    """连接健康墙单项：一个可观测组件的连接态快照（只读探查，不触发动作）。"""

    name: str
    state: DomainHealthState = "unknown"
    detail: str = ""
    last_error: str = ""
    supports_reconnect: bool = False


class HealthResponse(BaseModel):
    """健康检查响应

    ``status``/``timestamp_ms`` 是进程存活面（既有消费方依赖）；
    ``domains`` 是连接健康墙聚合（按域分组的组件连接态，可能为空——
    早期启动或聚合数据源缺失时只回答"进程活着"）。
    """

    status: str = "ok"
    timestamp_ms: int
    checked_at_ms: Optional[int] = None
    domains: Dict[str, List[DomainHealthItem]] = {}


class ChangelogResponse(BaseModel):
    """更新日志响应（CHANGELOG.md 原文，WebUI 侧渲染 markdown）。"""

    content: str
