"""game_2048 生产装配入口：棋盘与策略全部进程内，无外部后端可装配。

框架（factory）只负责"名字 → 包内 build"，装配知识由本包自持——
加内容 = 加包 + 配置，框架零改动。
"""

from __future__ import annotations

from src.modules.agents.deps import AgentDeps

from .agent import Game2048Agent
from .config import Game2048Config

__all__ = ["build"]


def build(config: Game2048Config, deps: AgentDeps) -> Game2048Agent:
    """按基建依赖装配 game_2048 Agent（生产构造的唯一装配入口）。

    Args:
        config: 已解析的 Game2048Config 实例
        deps: 基建依赖值对象（取 tool_registry / event_bus）

    Returns:
        未启动的 Game2048Agent 实例（生命周期由调用方管理）
    """
    return Game2048Agent(
        config,
        tool_registry=deps.tool_registry,
        event_bus=deps.event_bus,
    )
