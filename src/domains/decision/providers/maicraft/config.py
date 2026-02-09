"""
Maicraft Decision Provider 配置 Schema

定义配置验证和默认值。
"""

from typing import Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class MaicraftDecisionProviderConfig(BaseModel):
    """MaicraftDecisionProvider 配置 Schema"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "maicraft",
                "enabled": True,
                "factory_type": "log",
                "command_mappings": {
                    "chat": "chat",
                    "say": "chat",
                    "聊天": "chat",
                    "attack": "attack",
                    "攻击": "attack",
                },
                "command_prefix": "/",
                "mcp_server_url": "http://localhost:8080",
                "mcp_timeout": 30,
                "verbose_logging": False,
            }
        }
    )

    # Provider 类型标识
    type: Literal["maicraft"] = "maicraft"

    # 是否启用
    enabled: bool = True

    # 工厂类型：log（测试用）或 mcp（生产环境）
    factory_type: Literal["log", "mcp"] = "log"

    # 命令映射配置
    # 格式: {"聊天": "chat", "攻击": "attack"}
    command_mappings: Dict[str, str] = Field(
        default_factory=lambda: {
            "chat": "chat",
            "say": "chat",
            "聊天": "chat",
            "attack": "attack",
            "攻击": "attack",
        }
    )

    # 命令前缀
    command_prefix: str = "/"

    # MCP 配置（当 factory_type 为 "mcp" 时使用）
    mcp_server_url: Optional[str] = None
    mcp_timeout: int = 30

    # 是否输出详细日志
    verbose_logging: bool = False
