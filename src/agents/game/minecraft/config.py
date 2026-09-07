"""MinecraftAgent 配置

只放"该游戏特有"的配置（其它公用依赖经构造器注入，不走配置）。
默认值即可构造——不连真实 Minecraft 世界也可启动（空闲零消耗，无副作用）。
"""

from __future__ import annotations

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


class MinecraftConfig(BaseConfig):
    """MinecraftAgent 运行时配置

    Attributes:
        max_steps: 单任务内 ReAct 循环（LLM 推理步数）上限——防失控挂起
    """

    max_steps: int = Field(default=50, ge=1, description="单任务 ReAct 循环最大步数（超出挂起上报）")


__all__ = ["MinecraftConfig"]
