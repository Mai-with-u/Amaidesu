"""
Amaidesu 记忆模块

提供：
- ``MemoryProvider`` Protocol（接口稳定，未来换后端零改动）
- ``ViewerFact`` / ``ViewerProfile`` / ``ViewerProfileSummary`` 数据类
- ``SimpleMemory`` 实现：观众事实（viewer_facts）与画像（viewer_profiles）
  的 SQLite 读写服务 + 管理面（列表 / 纠正 / 删除，WebUI 画像管理页消费）
- ``query_memory`` / ``query_viewer_profile`` 工具（注册进 ToolRegistry；
  简单工具正典路径样板，见 ``query_tool.py``）
"""

from src.modules.memory.models import (
    MemoryHit,
    MemoryWriteResult,
    ViewerFact,
    ViewerProfile,
    ViewerProfileSummary,
)
from src.modules.memory.provider import MemoryProvider
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.memory.query_tool import build_memory_tools

__all__ = [
    "MemoryHit",
    "MemoryWriteResult",
    "ViewerFact",
    "ViewerProfile",
    "ViewerProfileSummary",
    "MemoryProvider",
    "SimpleMemory",
    "build_memory_tools",
]
