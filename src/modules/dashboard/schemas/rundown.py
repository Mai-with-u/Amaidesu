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
    """``GET /api/v1/rundown/state`` 响应。"""

    available: bool
    message: Optional[str] = None
    snapshot: Optional[Dict[str, Any]] = None
    transitions: List[Dict[str, Any]] = Field(default_factory=list)
    segments: List[RundownSegmentView] = Field(default_factory=list)
    config: RundownConfigView = Field(default_factory=RundownConfigView)


class RundownControlRequest(BaseModel):
    """``POST /api/v1/rundown/control`` 请求体。"""

    action: RundownControlAction
    segment_id: Optional[str] = None


class RundownControlResponse(BaseModel):
    """``POST /api/v1/rundown/control`` 响应。"""

    success: bool
    message: str
    snapshot: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 流程单库 CRUD（/api/v1/rundowns*）
# ---------------------------------------------------------------------------


class RundownDefinition(BaseModel):
    """流程单完整定义——库列表项与 upsert 请求体共用同一形状。

    完整性校验（环节 id 唯一、``min_duration_ms <= expected_ms``、时长下界）
    由后端 ``Rundown`` 模型负责，本层只做形状与非空约束。
    """

    rundown_id: str = Field(..., min_length=1, description="流程单唯一标识（存储主键、配置引用同一 id）")
    title: str = Field(..., min_length=1, description="流程单标题")
    segments: List[RundownSegmentView] = Field(..., min_length=1, description="环节列表，非空")


class RundownListResponse(BaseModel):
    """``GET /api/v1/rundowns`` 响应。"""

    success: bool
    message: str = ""
    rundowns: List[RundownDefinition] = Field(default_factory=list)
    current_id: str = Field(default="", description="配置当前指向的流程单 id；空 = 使用内置默认流程单")


class RundownTemplateResponse(BaseModel):
    """``GET /api/v1/rundowns/template`` 响应（新建预填模板）。"""

    success: bool
    message: str = ""
    definition: Optional[RundownDefinition] = None


class RundownMutateResponse(BaseModel):
    """流程单库写操作的统一响应（upsert / delete / duplicate / activate）。"""

    success: bool
    message: str = ""
    rundown_id: Optional[str] = None


__all__ = [
    "RundownControlAction",
    "RundownControlRequest",
    "RundownControlResponse",
    "RundownConfigView",
    "RundownDefinition",
    "RundownListResponse",
    "RundownMutateResponse",
    "RundownSegmentView",
    "RundownStateResponse",
    "RundownTemplateResponse",
]
