"""2048 游戏 Agent

``src/agents/game_2048/`` 自包含包，体现
"加内容 = 加包 + 配置，框架零改动" 的包设计。

当前可导入面：
- :class:`Game2048Agent` — Agent 主体（棋局宿主，按键驱动 + game.* 上报）
- :func:`build_game_2048_agent` — 便捷构造函数（依赖显式传参）
- :class:`Game2048Config` — Agent 配置 Schema（Pydantic；包内单一权威）
- :class:`Board2048` — 2048 规则引擎（纯逻辑，随机源构造注入）
- :class:`Game2048ToolProvider` — 工具面 Provider（press/get_state）
- :func:`build_game_2048_visible_to` — 两工具的可见名单映射（全部仅主播）
"""

from src.agents.game_2048.agent import Game2048Agent, build_game_2048_agent
from src.agents.game_2048.board import Board2048, MoveDirection
from src.agents.game_2048.config import Game2048Config
from src.agents.game_2048.tools import (
    Game2048ToolProvider,
    build_game_2048_visible_to,
)

__all__ = [
    # Agent 主体
    "Game2048Agent",
    "build_game_2048_agent",
    # 配置
    "Game2048Config",
    # 规则引擎
    "Board2048",
    "MoveDirection",
    # 工具面
    "Game2048ToolProvider",
    "build_game_2048_visible_to",
]
