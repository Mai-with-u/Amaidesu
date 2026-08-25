"""
组件信息 Schema

定义 组件相关的数据模型。
"""

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel


class ComponentControlAction(str, Enum):
    """组件控制动作"""

    START = "start"
    STOP = "stop"
    RESTART = "restart"


class ComponentSummary(BaseModel):
    """组件摘要信息"""

    name: str
    phase: str  # input/decision/output（v2 group 是主字段，phase 保留兼容）
    type: str
    is_started: bool
    is_enabled: bool
    group: Optional[str] = None  # v2 分组：collectors/agents/tools


class ComponentDetail(BaseModel):
    """组件详情"""

    name: str
    phase: str
    type: str
    is_started: bool
    is_enabled: bool
    config: Optional[Dict[str, Any]] = None
    stats: Optional[Dict[str, Any]] = None


class ComponentListResponse(BaseModel):
    """组件列表响应（v2：collectors/agents/tools 三组 + 旧阶段兼容字段）"""

    # v2 分组（前端主数据源）
    collectors: list[ComponentSummary] = []
    agents: list[ComponentSummary] = []
    tools: list[ComponentSummary] = []
    # 旧阶段兼容（deprecated，前端有 phase→group 兜底）
    input: list[ComponentSummary] = []
    decision: list[ComponentSummary] = []
    output: list[ComponentSummary] = []


class ComponentDetailResponse(BaseModel):
    """组件详情响应"""

    component: ComponentDetail


class ComponentControlRequest(BaseModel):
    """组件控制请求"""

    action: ComponentControlAction


class ComponentControlResponse(BaseModel):
    """组件控制响应"""

    success: bool
    message: str
