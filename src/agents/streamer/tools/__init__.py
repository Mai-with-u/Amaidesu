"""主播 Agent 专属工具壳层（Agent 内部 builtin Provider）。

三个模块均为**真工具**（``provider="builtin"``），只做"包装内脏 → LLM 可调工具"
的薄壳：本身不含决策/表达逻辑，被包装的内脏（Replyer / ProactiveTrigger /
command 解析原语）留在上层目录，确保"Planner/Replyer 等内脏永不注册为工具"
红线的物理边界清晰。

- ``reply_tool``        - ``reply`` 工具入口（包装 Replyer 表达引擎）
- ``proactive_tool``    - ``should_speak_proactively`` 工具入口（包装 ProactiveTrigger）
- ``command_tool``      - ``parse_command`` 工具入口（包装 ``../command/`` 纯解析原语，只解析不执行）
"""

from .command_tool import CommandToolProvider, build_command_tool_spec, register_command_tool
from .proactive_tool import (
    ProactiveToolProvider,
    build_proactive_tool_spec,
    register_proactive_tool,
)
from .reply_tool import build_reply_tool_invoker, build_reply_tool_spec, register_reply_tool

__all__ = [
    "CommandToolProvider",
    "build_command_tool_spec",
    "register_command_tool",
    "ProactiveToolProvider",
    "build_proactive_tool_spec",
    "register_proactive_tool",
    "build_reply_tool_invoker",
    "build_reply_tool_spec",
    "register_reply_tool",
]
