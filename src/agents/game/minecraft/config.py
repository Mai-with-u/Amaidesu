"""MinecraftAgent 配置

只放"该游戏特有"的配置（其它公用依赖经构造器注入，不走配置）。
默认值即可构造——不连真实 Minecraft 世界也可启动（决策循环自证无副作用）。
"""

from __future__ import annotations

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


class MinecraftConfig(BaseConfig):
    """MinecraftAgent 配置

    Attributes:
        engine_kind: 游戏引擎标识（仅日志/多实例区分，不参与工具分发）
        tick_seconds: 决策循环轮询间隔（秒）
        server_id: maicraft MCP 侧 server 别名（工具调用透传路由参数）
    """

    engine_kind: str = Field(default="minecraft", description="游戏引擎标识")
    tick_seconds: float = Field(default=8.0, ge=0.5, description="决策循环轮询间隔（秒）")
    server_id: str = Field(default="minecraft-server", description="maicraft MCP server 别名（透传路由参数）")


__all__ = ["MinecraftConfig"]
