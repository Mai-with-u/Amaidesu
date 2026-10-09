"""MinecraftAgent —— 用 MaiCraft v1 的工具玩 Minecraft 的 AI 玩家（普通 ReAct Agent）

自包含包（内容特有逻辑内聚，框架零改动）：
- ``config.py``              运行时配置（上下文预算、事件长轮询、私有 MCP）
- ``state.py``               Agent 内存状态（todo/notebook/reports）
- ``tools.py``               局部工具（todo/notebook/report/wait/get_work_log/glance/skill）Spec + Provider
- ``maicraft.py``            MaiCraft v1 的返回格式、目标运行状态与事件流，只在这里解读一次
- ``goals.py``               后台目标跟踪：events 长轮询，目标需要处理时唤醒任务
- ``glance.py``              主播看一眼的叙事视图
- ``context.py``             任务历史按预算集中整理
- ``agent.py``               MinecraftAgent（BaseAgent）：命令驱动 ReAct 循环
- ``prompts/``               系统提示词
- ``skills/``                技能（玩法经验文档），按能力清单筛选后进目录

两个采集器常驻长轮询 MaiCraft v1 的 ``events``（连接、游标与长轮询在 ``events_collector.py``）：
``attention_collector.py`` 读任务事件流，把身体先处理的急事、处理不了的需求与角色死亡转成 ``game.body.*``
事件（分类表在 ``attention_matrix.py``）；``chat_collector.py`` 读聊天事件流，把别人说的话转成
``game.chat.received``（私聊默认不转）。装配与起停走采集器框架（``config/collectors.toml`` + 工厂），
生命周期挂装配期而非本 Agent。
"""

from .agent import MinecraftAgent
from .config import MinecraftConfig

__all__ = ["MinecraftAgent", "MinecraftConfig"]
