"""TextAdvGameAgent 配置（包内单一权威）

只放"该游戏特有"的配置（其它公用依赖经构造器注入，不走配置）。
默认值即可构造——不连真实文字冒险世界也可启动（空闲零消耗，无副作用）。
"""

from __future__ import annotations

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


class TextAdvConfig(BaseConfig):
    """TextAdvGameAgent 运行时配置

    Attributes:
        engine_kind: 内容引擎标识（默认 text_adv；用于多实例区分/日志）
        decision_strategy: 推进策略（first_option=首选项；llm=LLM 选择——待实现）
        enable_event_emission: 是否在感知/推进时 emit game.* 事件
    """

    engine_kind: str = Field(default="text_adv", description="内容引擎标识")
    decision_strategy: str = Field(
        default="first_option",
        description="推进策略（first_option=首选项；llm=LLM 选择——待实现）",
    )
    enable_event_emission: bool = Field(
        default=True,
        description="是否在感知/推进时 emit game.* 事件",
    )


__all__ = ["TextAdvConfig"]
