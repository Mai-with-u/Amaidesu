"""Game2048Agent 配置（包内单一权威）

只放"该游戏特有"的运行参数（棋盘规则硬编码为经典 2048；按键驱动，
无自动走子可配置）。默认值即可构造，无任何副作用。
"""

from __future__ import annotations

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


class Game2048Config(BaseConfig):
    """Game2048Agent 运行时配置

    Attributes:
        milestone_tile: 里程碑播报的起始块值（首次合成出 ≥ 该值的新档位
            发 ``game.milestone``，此后每翻倍一档再播一次）
    """

    milestone_tile: int = Field(
        default=256,
        title="里程碑起始块值",
        ge=8,
        description="里程碑播报起始块值（首次达到后每翻倍一档再播）",
    )


__all__ = ["Game2048Config"]
