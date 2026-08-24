"""Amaidesu 游戏 Agent 包（v2.0.0 / Wave 7）

按架构 §1.52 定案：
- ``src/agents/game/`` 是游戏 Agent 顶层包
- 每个具体游戏一个子包（``text_adv`` / 未来 ``minecraft`` / ``stardew_valley`` / ...）
- 每个子包自包含：感知（调公用 look_at_screen）+ 推进（专属工具）+ 状态（自有）+ 循环

判别口诀（§1.2 "插件 vs Agent 包"）：
- Agent 包 = 主体开放内聚包（只带自我，能力全借框架，Agent 间零依赖）
- 框架零改动加新游戏 = 范式验证目标

Wave 7 落地示例：``text_adv``（文字冒险）。
"""

from src.agents.game.text_adv import (
    TextAdvGameAgent,
    TextAdvGameAgentState,
    TextAdvGameConfig,
    TextAdvToolProvider,
    build_text_adv_agent,
)

__all__ = [
    "TextAdvGameAgent",
    "TextAdvGameAgentState",
    "TextAdvGameConfig",
    "TextAdvToolProvider",
    "build_text_adv_agent",
]
