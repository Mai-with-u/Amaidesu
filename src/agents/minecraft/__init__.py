"""MinecraftAgent —— Minecraft 世界中的 AI 玩家（普通 ReAct Agent）

自包含包（内容特有逻辑内聚，框架零改动）：
- ``config.py``              运行时配置（max_steps 防失控）
- ``state.py``               Agent 内存状态（todo/notebook/reports）
- ``tools.py``               局部工具（todo/notebook/get_work_log/report）Spec + Provider
- ``agent.py``               MinecraftAgent（BaseAgent）：命令驱动 ReAct 循环
- ``attention_collector.py`` 注意流采集器（身体事件 → game.body.* 事件）
- ``prompts/``               系统提示词（ReAct 工作方式引导）

``attention_collector.py`` 是**游戏相关**的外部世界适配器，故随本包内聚；
但它的装配与起停走采集器框架（``config/collectors.toml`` + 工厂），
生命周期挂装配期而非本 Agent——这样"待机时也有身体事件流"成立，
且游戏 Agent 重建/停机不会带走这条流。
"""

from .agent import MinecraftAgent
from .config import MinecraftConfig

__all__ = ["MinecraftAgent", "MinecraftConfig"]
