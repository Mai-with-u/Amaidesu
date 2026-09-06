"""
Amaidesu Context 模块

本目录并行提供两套 API：
- **ContextAssembler**：纯函数上下文组装（无循环、无定时、无 LLM）
- **ContextService**：对话上下文管理服务（既有 API，保留避免破坏现有 import 路径）

## 提供
- ``ContextAssembler``：纯函数组件（无循环、无定时、无 LLM）
- ``Snapshot``：组装快照（稳定→动态排序 / 时间块分钟级置尾 / 直播流非对话历史）
- ``AssembledSection``：section 单元（带稳定性 hint 便于 LLM 缓存前缀优先）
- ``PlannerAssembler``：每类 Agent 一个 Assembler 实例

## 兼容导出
- ``ContextService`` / ``ContextServiceConfig`` / ``ConversationMessage`` /
  ``MessageRole`` / ``SessionInfo`` / ``StorageType`` —— 既有 API 仍可被
  现有代码引用。
"""

# ContextService API（保留供既有模块使用）
from src.modules.context.config import ContextServiceConfig, StorageType
from src.modules.context.models import (
    ConversationMessage,
    MessageRole,
    SessionInfo,
)
from src.modules.context.service import ContextService

# 纯函数 ContextAssembler API
from src.modules.context.assembler import (
    AssembledSection,
    AssemblerInputs,
    ContextAssembler,
    PlannerAssembler,
    SectionStability,
    Snapshot,
)
from src.modules.context.snapshot import EnvironmentBlock, TimelineBlock, WorkingMemoryTrace

__all__ = [
    # ContextService（与 ContextAssembler 并行共存）
    "ContextService",
    "ContextServiceConfig",
    "ConversationMessage",
    "MessageRole",
    "SessionInfo",
    "StorageType",
    # 纯函数 ContextAssembler
    "AssembledSection",
    "AssemblerInputs",
    "ContextAssembler",
    "PlannerAssembler",
    "Snapshot",
    "SectionStability",
    "EnvironmentBlock",
    "TimelineBlock",
    "WorkingMemoryTrace",
]
