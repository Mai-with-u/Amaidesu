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
    phase: str  # input/decision/output
    type: str
    is_started: bool
    is_enabled: bool


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
    """组件列表响应"""

    input: list[ComponentSummary]
    decision: list[ComponentSummary]
    output: list[ComponentSummary]


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
