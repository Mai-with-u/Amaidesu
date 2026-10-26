"""工具域请求契约

工具提供者面板的写路径请求体（开关控制、调试调用）。
"""

from typing import Any, Dict, Literal

from pydantic import BaseModel, Field


class ProviderControlRequest(BaseModel):
    """提供者/工具开关控制请求体。"""

    action: Literal["enable", "disable"]


class ToolInvokeRequest(BaseModel):
    """工具调试调用请求体（arguments 形状由目标工具的 parameters_schema 约定）。"""

    arguments: Dict[str, Any] = Field(default_factory=dict)
