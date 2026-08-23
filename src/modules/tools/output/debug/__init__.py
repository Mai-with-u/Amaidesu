"""调试工具模块（Wave 4 替换 DebugConsoleHandler）

- ``dump_intent(intent, config)`` - 格式化打印 Intent 到 stdout
- ``DebugConfig`` - 控制打印项开关（与原 schema 字段对应）
"""

from .debug_dump import DebugConfig, dump_intent

__all__ = ["dump_intent", "DebugConfig"]
