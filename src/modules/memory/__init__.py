"""
Amaidesu 记忆模块

提供：
- ``MemoryProvider`` Protocol（接口稳定，未来换后端零改动）
- ``MemoryHit`` / ``MemoryWriteResult`` 数据类
- ``SimpleMemory`` 实现：SQLite 存储 + 关键词召回（无 embedding）
- ``query_memory`` 工具（注册进 ToolRegistry；简单工具正典路径样板，
  见 ``query_tool.py``）
"""

from src.modules.memory.models import (
    MemoryHit,
    MemoryWriteResult,
)
from src.modules.memory.provider import MemoryProvider
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.memory.query_tool import build_query_memory_tool

__all__ = [
    "MemoryHit",
    "MemoryWriteResult",
    "MemoryProvider",
    "SimpleMemory",
    "build_query_memory_tool",
]
