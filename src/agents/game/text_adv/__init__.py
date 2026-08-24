"""文字冒险游戏 Agent —— Wave 7 范式验证示例

本子包是 ``src/agents/game/`` 下的**第一个**示例游戏 Agent，用于验证
"加内容 = 加包 + 配置，框架零改动" 范式（§1.49 / §1.52 / §1.5.1）。

组成（全部自包含，无外部游戏特定依赖）：
- :class:`TextAdvGameConfig` — Agent 配置 Schema（Pydantic）
- :class:`TextAdvGameAgentState` — 游戏内部状态（§1.31 内容状态内部自由）
- :class:`TextAdvToolProvider` — Agent 专属工具（``choose_option`` / ``get_story``）
- :class:`TextAdvGameAgent` — 主 Agent（继承 BaseAgent）

可被复用的框架能力（**不重写**）：
- 感知 → 调公用 ``look_at_screen`` 工具（``modules/tools/perception/``）
- 控制 → 调公用 ``content_engine_*`` 工具（``modules/tools/content_engine/``）
- 事件 → emit ``game.*`` 语义域事件（``events/names.py`` + ``events/payloads/game.py``）
- 生命周期 → BaseAgent 六面协议（``modules/agents/base.py``）
"""

from src.agents.game.text_adv.agent import (
    TextAdvGameAgent,
    TextAdvGameConfig,
    build_text_adv_agent,
)
from src.agents.game.text_adv.state import TextAdvGameAgentState
from src.agents.game.text_adv.tools import (
    TextAdvToolProvider,
    build_choose_option_spec,
    build_get_story_spec,
)

__all__ = [
    # 配置
    "TextAdvGameConfig",
    # 状态
    "TextAdvGameAgentState",
    # 工具
    "TextAdvToolProvider",
    "build_choose_option_spec",
    "build_get_story_spec",
    # Agent
    "TextAdvGameAgent",
    "build_text_adv_agent",
]
