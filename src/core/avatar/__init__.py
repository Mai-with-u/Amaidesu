"""
通用虚拟形象控制模块

该模块提供了一个统一的抽象层，用于控制不同的虚拟形象平台（如 VTS、VRChat、Live2D 等）。

主要组件：
- AvatarAdapter: 适配器基类，定义统一的接口
- AvatarControlManager: 核心控制器，管理所有适配器
- SemanticActionMapper: 语义动作映射器
- ToolGenerator: LLM 工具生成器
- LLMExecutor: 工具调用执行器

使用示例：
    from src.core.avatar import AvatarControlManager

    # 获取管理器
    manager = core.get_service("avatar_control_manager")

    # 获取 LLM 工具定义
    tools = await manager.generate_llm_tools()

    # 执行工具调用
    await manager.execute_tool_call("set_avatar_expression", {"expression": "happy", "intensity": 0.8})
"""

from .adapter_base import AvatarAdapter, ParameterMetadata, ActionMetadata
from .avatar_manager import AvatarControlManager
from .semantic_actions import SemanticActionMapper
from .tool_generator import ToolGenerator
from .llm_executor import LLMExecutor

__all__ = [
    "AvatarAdapter",
    "ParameterMetadata",
    "ActionMetadata",
    "AvatarControlManager",
    "SemanticActionMapper",
    "ToolGenerator",
    "LLMExecutor",
]

__version__ = "1.0.0"
