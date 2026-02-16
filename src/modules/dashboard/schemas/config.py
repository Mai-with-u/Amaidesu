"""
配置 Schema

定义配置相关的数据模型。
"""

from typing import Any, Dict

from pydantic import BaseModel


class ConfigResponse(BaseModel):
    """配置响应"""

    general: Dict[str, Any] = {}
    providers: Dict[str, Any] = {}
    pipelines: Dict[str, Any] = {}
    logging: Dict[str, Any] = {}
    context: Dict[str, Any] = {}
    dashboard: Dict[str, Any] = {}


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""

    updates: Dict[str, Any]


class ConfigUpdateResponse(BaseModel):
    """配置更新响应"""

    success: bool
    message: str
