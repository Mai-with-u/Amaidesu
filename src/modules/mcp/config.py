"""
MCP Server 配置模型

定义 MCP 服务的配置结构。
"""

from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    """MCP Server 配置"""

    enabled: bool = Field(default=False, description="是否启用 MCP 服务")
    host: str = Field(default="127.0.0.1", description="MCP 服务监听地址")
    port: int = Field(default=8021, description="MCP 服务监听端口（有效范围：1024-65535）")
