"""
MCP Server 配置模型

定义 MCP 服务的配置结构。
"""

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


class MCPServerConfig(BaseConfig):
    """MCP Server 配置"""

    enabled: bool = Field(default=False, description="是否启用 MCP 服务")
    host: str = Field(default="127.0.0.1", description="MCP 服务监听地址")
    port: int = Field(default=30214, ge=1024, le=65535, description="MCP 服务监听端口（有效范围：1024-65535）")
