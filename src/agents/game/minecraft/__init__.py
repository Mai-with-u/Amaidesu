"""MinecraftAgent —— Minecraft 世界中的 AI 玩家

自包含包（内容特有逻辑内聚，框架零改动）：
- ``config.py``        配置 Schema（tick_seconds 等）
- ``state.py``         Agent 内存状态（todo/memo/milestones/current_goal）
- ``tools.py``         局部工具（mc_todo / mc_memo / mc_get_state）Spec + Provider
- ``maicraft_adapter.py``  slim 适配（唯一知道 maicraft_* 工具名 / 参数 / 返回结构的地方）
- ``agent.py``         MinecraftAgent（BaseAgent）：决策循环
"""

from .agent import MinecraftAgent, MinecraftConfig, build_minecraft_agent

__all__ = ["MinecraftAgent", "MinecraftConfig", "build_minecraft_agent"]
