"""
Amaidesu 记忆模块

提供：
- ``MemoryProvider`` Protocol（接口稳定，后插 AMemorixProvider 零改动）
- ``MemoryHit`` / ``MemoryWriteResult`` / ``PersonProfile`` 数据类
- ``SimpleMemory`` 实现：SQLite 存储 + 关键词召回（无 embedding）
- ``query_memory`` 工具（注册进 ToolRegistry）
- ``build_query_memory_tool`` 工厂：把 query_memory 工具包成一个 ToolProvider
"""

from src.modules.memory.models import (
    MemoryHit,
    MemoryWriteResult,
    PersonProfile,
)
from src.modules.memory.provider import MemoryProvider
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.memory.query_tool import (
    QueryMemoryToolProvider,
    build_query_memory_tool,
    query_memory,
)

__all__ = [
    "MemoryHit",
    "MemoryWriteResult",
    "PersonProfile",
    "MemoryProvider",
    "SimpleMemory",
    "QueryMemoryToolProvider",
    "build_query_memory_tool",
    "query_memory",
]
