"""
Dashboard 配置模型

定义 Dashboard 服务器的配置结构。
"""

from typing import List

from pydantic import BaseModel, Field


class DashboardConfig(BaseModel):
    """Dashboard 配置"""

    enabled: bool = Field(default=True, description="是否启用 Dashboard")
    host: str = Field(default="127.0.0.1", description="监听地址")
    port: int = Field(default=60214, description="监听端口")
    cors_origins: List[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        description="CORS 允许的源",
    )
    max_history_messages: int = Field(default=1000, description="内存中保留的最大历史消息数")
    websocket_heartbeat: int = Field(default=30, description="WebSocket 心跳间隔（秒）")
