"""文字冒险游戏 Agent

``src/agents/text_adv/`` 自包含包，体现
"加内容 = 加包 + 配置，框架零改动" 的包设计。

当前可导入面：
- :class:`TextAdvGameAgent` — Agent 主体（auto 标志 + 观察循环 + game.* 上报）
- :func:`build_text_adv_agent` — 便捷构造函数（依赖显式传参）
- :class:`TextAdvConfig` — Agent 配置 Schema（Pydantic；包内单一权威）
- :class:`TextAdvGameAgentState` — 游戏内部状态（内存态 + 环缓冲 + 快照导出）
- :class:`TextAdvToolProvider` — 工具面 Provider（advance/choose/set_auto/get_state）
- :class:`ScreenSession` — 观察会话（屏幕感知链采集语义的单点）
- :class:`MonitorGeometry` — 监视器虚拟桌面几何（session 模块承载）
- :class:`Option` / :class:`ScreenReading` — 识别契约的数据模型（vlm 模块承载）
"""

from src.agents.text_adv.agent import TextAdvGameAgent, build_text_adv_agent
from src.agents.text_adv.build import build
from src.agents.text_adv.config import TextAdvConfig
from src.agents.text_adv.session import MonitorGeometry, ScreenSession
from src.agents.text_adv.state import TextAdvGameAgentState
from src.agents.text_adv.tools import (
    TextAdvToolProvider,
    build_advance_spec,
    build_choose_spec,
    build_get_state_spec,
    build_set_auto_spec,
)
from src.agents.text_adv.vlm import Option, ScreenReading

__all__ = [
    # Agent 主体
    "TextAdvGameAgent",
    "build_text_adv_agent",
    # 生产装配入口
    "build",
    # 配置
    "TextAdvConfig",
    # 状态
    "TextAdvGameAgentState",
    # 观察会话
    "MonitorGeometry",
    "ScreenSession",
    # 工具面
    "TextAdvToolProvider",
    "build_advance_spec",
    "build_choose_spec",
    "build_set_auto_spec",
    "build_get_state_spec",
    # 识别契约数据模型
    "Option",
    "ScreenReading",
]
