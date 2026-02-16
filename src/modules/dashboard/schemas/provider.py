"""
Provider 信息 Schema

定义 Provider 相关的数据模型。
"""

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel


class ProviderControlAction(str, Enum):
    """Provider 控制动作"""

    START = "start"
    STOP = "stop"
    RESTART = "restart"


class ProviderSummary(BaseModel):
    """Provider 摘要信息"""

    name: str
    domain: str  # input/decision/output
    type: str
    is_started: bool
    is_enabled: bool


class ProviderDetail(BaseModel):
    """Provider 详情"""

    name: str
    domain: str
    type: str
    is_started: bool
    is_enabled: bool
    config: Optional[Dict[str, Any]] = None
    stats: Optional[Dict[str, Any]] = None


class ProviderListResponse(BaseModel):
    """Provider 列表响应"""

    input: list[ProviderSummary]
    decision: list[ProviderSummary]
    output: list[ProviderSummary]


class ProviderDetailResponse(BaseModel):
    """Provider 详情响应"""

    provider: ProviderDetail


class ProviderControlRequest(BaseModel):
    """Provider 控制请求"""

    action: ProviderControlAction


class ProviderControlResponse(BaseModel):
    """Provider 控制响应"""

    success: bool
    message: str
