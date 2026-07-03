"""CommandDecider 配置 Schema"""

from typing import Dict

from pydantic import BaseModel, ConfigDict, Field


class CommandDeciderConfig(BaseModel):
    """CommandDecider 配置 Schema"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "command",
                "command_mappings": {
                    "chat": "chat",
                    "say": "chat",
                    "聊天": "chat",
                    "attack": "attack",
                    "攻击": "attack",
                },
                "command_prefix": "/",
            }
        }
    )

    command_mappings: Dict[str, str] = Field(
        default_factory=lambda: {
            "chat": "chat",
            "say": "chat",
            "聊天": "chat",
            "attack": "attack",
            "攻击": "attack",
        },
        description="命令映射配置：{命令名 → 动作字符串}",
    )
    command_prefix: str = Field(default="/", description="命令前缀")
