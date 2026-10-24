"""Agent 构造依赖值对象（AgentDeps）。

启动装配（main）与 Dashboard 动态启用（``AgentManager.enable_agent``）
共用的基建透传清单：字段与 Agent 构造所需一一对应。清单单点定义在
本模块——新增依赖改这里加字段 + 真正消费它的 Agent 形参即可，
factory 与调用方不再各自抄写签名。

字段语义按消费侧分两档：
- 调用方侧（main / ``enable_agent`` 的调用者）：None = 未指定。仓储与
  注册表类字段由 ``AgentManager`` 回退到构造时注入的成员默认（显式
  传参覆盖、未传回落默认）。
- 工厂侧（``factory.instantiate_agent``）：收到的是已解析的有效值，
  None = 该基建确实未装配，Agent 按各自降级语义处理（如 chat_repo
  缺失 = 对话历史读取整体短路）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.modules.tools.registry import ToolRegistry

__all__ = ["AgentDeps"]


@dataclass(slots=True)
class AgentDeps:
    """enable_agent 的依赖集合（值对象）。

    字段与 Agent 构造所需的基础设施透传一一对应；全部 Optional 默认
    None——调用侧 None 表示未显式指定，由 AgentManager 回退到构造时
    注入的成员默认（显式传参覆盖、未传回落默认）。
    """

    llm_manager: Optional[object] = None
    prompt_manager: Optional[object] = None
    event_bus: Optional[object] = None
    tool_registry: Optional[ToolRegistry] = None
    thinking_sink: Optional[Any] = None
    speech_config: Optional[Dict[str, Any]] = None
    tts_engine: Optional[Any] = None
    subtitle_service: Optional[Any] = None
    session_manager: Optional[Any] = None
    rundown_repo: Optional[Any] = None
    rundown_instance_repo: Optional[Any] = None
    rundown_transition_repo: Optional[Any] = None
    chat_repo: Optional[Any] = None
    sessions_repo: Optional[Any] = None
    topic_repo: Optional[Any] = None
    viewer_repo: Optional[Any] = None
    viewer_profile_repo: Optional[Any] = None
    recap_repo: Optional[Any] = None
    event_repo: Optional[Any] = None
    context_assembler_config: Optional[Any] = None
    task_tracker: Optional[Any] = None
    task_ledger: Optional[Any] = None
