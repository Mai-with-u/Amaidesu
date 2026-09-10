"""流程单（Rundown）编排页 API Schema

定义流程单编排 Dashboard 接口的请求/响应数据模型。

设计要点
--------
- snapshot / transitions / segments 内部结构保持为 ``dict`` 透传 —— RundownState
  已有稳定契约，外层包一层强类型外壳。
- 控制动作返回值（success/message/snapshot）沿用 ``{success, message}`` 模式；
  snapshot 字段为可选，用于前端刷新整场视图。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RundownControlAction(str, Enum):
    """流程单控制动作（Dashboard 手动操作，by="human"）。"""

    PAUSE = "pause"
    RESUME = "resume"
    NEXT = "next"
    GOTO = "goto"


class RundownSegmentView(BaseModel):
    """流程单单个环节的 Dashboard 视图。"""

    id: str
    title: str
    task_description: str = ""
    key_points: List[str] = Field(default_factory=list)
    expected_ms: int = 0
    min_duration_ms: Optional[int] = None
    notes: Optional[str] = None


class RundownConfigView(BaseModel):
    """流程单配置只读展示（来自 agents.streamer）。"""

    rundown_id: str = ""


class RundownStateResponse(BaseModel):
    """``GET /api/v1/agenda/state`` 响应。"""

    available: bool
    message: Optional[str] = None
    snapshot: Optional[Dict[str, Any]] = None
    transitions: List[Dict[str, Any]] = Field(default_factory=list)
    segments: List[RundownSegmentView] = Field(default_factory=list)
    config: RundownConfigView = Field(default_factory=RundownConfigView)


class RundownControlRequest(BaseModel):
    """``POST /api/v1/agenda/control`` 请求体。"""

    action: RundownControlAction
    segment_id: Optional[str] = None


class RundownControlResponse(BaseModel):
    """``POST /api/v1/agenda/control`` 响应。"""

    success: bool
    message: str
    snapshot: Optional[Dict[str, Any]] = None


__all__ = [
    "RundownControlAction",
    "RundownControlRequest",
    "RundownControlResponse",
    "RundownConfigView",
    "RundownSegmentView",
    "RundownStateResponse",
]
