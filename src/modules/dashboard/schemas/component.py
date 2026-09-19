"""
组件信息 Schema

定义 组件相关的数据模型。
"""

from enum import Enum
from typing import Optional

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
    description: str = ""  # 描述（来自管理器注册/工具规格；前端可选展示）


class ComponentListResponse(BaseModel):
    """组件列表响应（v2：collectors / agents 两组）

    工具不在此清单：工具以"域开关单元"管理（tools API 的 domains 端点）。
    """

    collectors: list[ComponentSummary] = []
    agents: list[ComponentSummary] = []


class ComponentControlRequest(BaseModel):
    """组件控制请求"""

    action: ComponentControlAction


class ComponentControlResponse(BaseModel):
    """组件控制响应"""

    success: bool
    message: str
