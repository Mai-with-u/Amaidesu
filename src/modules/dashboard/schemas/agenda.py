"""Agenda 工作台 API Schema（v2 Agenda 子系统）

定义节目单工作台 Dashboard 接口的请求/响应数据模型。

设计要点
--------
- snapshot / transitions / segments 内部结构保持为 ``dict`` 透传 —— AgendaState
  已有稳定契约，外层包一层强类型外壳。
- 控制动作返回值（success/message/snapshot）沿用 components.py 的 ``{success, message}``
  模式；snapshot 字段为可选，用于前端刷新整场视图。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgendaControlAction(str, Enum):
    """Agenda 控制动作。"""

    PAUSE = "pause"
    RESUME = "resume"
    SKIP = "skip"
    REWIND = "rewind"
    JUMP = "jump"
    UNLOAD = "unload"
    START = "start"


class AgendaSegmentView(BaseModel):
    """节目单单个环节的 Dashboard 视图。"""

    id: str
    title: str
    duration_ms: int
    min_duration_ms: Optional[int] = None
    task_description: str = ""
    key_points: List[str] = Field(default_factory=list)
    branch_count: int = 0
    expanded: bool = False
    needs_expansion: bool = False


class AgendaExpandedView(BaseModel):
    """环节扩展内容（来自 agenda_loader / AgendaLoader.ExpandedSegment）。"""

    opening_line: str = ""
    topic_guidance: str = ""
    talking_points: List[str] = Field(default_factory=list)


class AgendaConfigView(BaseModel):
    """Agenda 配置只读展示（来自 agents.streamer）。"""

    agenda_enabled: bool = False
    agenda_path: str = ""
    agenda_auto_start: bool = True


class AgendaStateResponse(BaseModel):
    """``GET /api/v1/agenda/state`` 响应。"""

    available: bool
    message: Optional[str] = None
    snapshot: Optional[Dict[str, Any]] = None
    transitions: List[Dict[str, Any]] = Field(default_factory=list)
    segments: List[AgendaSegmentView] = Field(default_factory=list)
    expanded: Dict[str, Optional[AgendaExpandedView]] = Field(default_factory=dict)
    config: AgendaConfigView = Field(default_factory=AgendaConfigView)


class AgendaControlRequest(BaseModel):
    """``POST /api/v1/agenda/control`` 请求体。"""

    action: AgendaControlAction
    segment_id: Optional[str] = None
    path: Optional[str] = None


class AgendaControlResponse(BaseModel):
    """``POST /api/v1/agenda/control`` 响应。"""

    success: bool
    message: str
    snapshot: Optional[Dict[str, Any]] = None


__all__ = [
    "AgendaControlAction",
    "AgendaControlRequest",
    "AgendaControlResponse",
    "AgendaConfigView",
    "AgendaExpandedView",
    "AgendaSegmentView",
    "AgendaStateResponse",
]
