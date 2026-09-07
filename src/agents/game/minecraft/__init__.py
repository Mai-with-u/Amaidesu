"""MinecraftAgent —— Minecraft 世界中的 AI 玩家（普通 ReAct Agent）

自包含包（内容特有逻辑内聚，框架零改动）：
- ``config.py``        运行时配置（max_steps 防失控）
- ``state.py``         Agent 内存状态（todo/notebook/milestones）
- ``tools.py``         局部工具（todo/notebook/get_state/assign）Spec + Provider
- ``agent.py``         MinecraftAgent（BaseAgent）：命令驱动 ReAct 循环
- ``prompts/``         系统提示词（ReAct 工作方式引导）
"""

from .agent import MinecraftAgent
from .config import MinecraftConfig

__all__ = ["MinecraftAgent", "MinecraftConfig"]
