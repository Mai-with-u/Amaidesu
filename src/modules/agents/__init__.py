"""
Amaidesu Agent 模块（Wave 3 / §1.49 定案）

提供：
- ``BaseAgent`` —— 框架对 Agent 的唯一最小契约（协议六面）
- ``AgentManager`` —— 统一注册 / 启动 / 监控 / 重启所有 Agent
- ``AgentControl`` —— 框架级 pause/resume/shutdown/restart 工具（provider=builtin）

权威参考：.omo/drafts/amaidesu-v2-architecture.md §1.49 + §1.50 + §1.52

## 协议六面（最小契约）

| # | 面 | 内容 |
|---|---|---|
| 1 | 生命周期 | start/stop/cleanup + 可重建性 |
| 2 | 工具提供 | list_tools() → 暴露工具 |
| 3 | 事件上报 | 自由 emit（+ 可选事件族声明） |
| 4 | 状态读写 | 框架给**状态写入口**，按 Agent 名字空间隔离 |
| 5 | 健康 | 统一心跳协议 |
| 6 | 元数据 | name/description |
"""

from src.modules.agents.base import AgentState, BaseAgent
from src.modules.agents.control import AgentControl, AgentControlProvider, build_agent_control_provider
from src.modules.agents.manager import AgentManager

__all__ = [
    "AgentState",
    "BaseAgent",
    "AgentControl",
    "AgentControlProvider",
    "build_agent_control_provider",
    "AgentManager",
]
