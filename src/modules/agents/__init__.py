"""
Amaidesu Agent 模块

提供：
- ``BaseAgent`` —— 框架对 Agent 的唯一最小契约（协议六面）
- ``AgentManager`` —— 统一注册 / 启动 / 监控 / 重启所有 Agent
- ``AgentControl`` —— 框架级 pause/resume/shutdown/restart 工具（provider=builtin）

## 协议六面（最小契约）

- 生命周期：start/stop/cleanup + 可重建性
- 工具提供：list_tools() → 暴露工具
- 事件上报：自由 emit（+ 可选事件族声明）
- 状态读写：框架给**状态写入口**，按 Agent 名字空间隔离
- 健康：统一心跳协议
- 元数据：name/description
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
