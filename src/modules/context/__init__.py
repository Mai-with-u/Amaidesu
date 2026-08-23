"""
Amaidesu Context 模块（Wave 3 新增 + 旧 ContextService 并行共存）

Wave 3 仅**纯新增** ContextAssembler 纯函数，与该目录下旧 ``ContextService``
**并行共存**，不删旧代码（§1.44 定案：旧路径需受 W4 业务迁移收口）。

## 提供
- ``ContextAssembler``：纯函数组件（无循环、无定时、无 LLM）
- ``Snapshot``：组装快照（稳定→动态排序 / 时间块分钟级置尾 / 直播流非对话历史）
- ``AssembledSection``：section 单元（带稳定性 hint 便于 LLM 缓存前缀优先）
- ``PlannerAssembler`` / ``ReplyerAssembler``：每类 Agent 一个 Assembler 实例

## 旧兼容
- ``ContextService`` / ``ContextServiceConfig`` / ``ConversationMessage`` /
  ``MessageRole`` / ``SessionInfo`` / ``StorageType`` —— 旧 API 仍可被旧代码
  引用，避免破坏 W3 外的现有 import 路径。

权威参考：.omo/drafts/amaidesu-v2-architecture.md §1.44
"""

# 旧 ContextService API（保留供旧模块使用）
from src.modules.context.config import ContextServiceConfig, StorageType
from src.modules.context.models import (
    ConversationMessage,
    MessageRole,
    SessionInfo,
)
from src.modules.context.service import ContextService

# Wave 3 新增 API（纯函数 ContextAssembler）
from src.modules.context.assembler import (
    AssembledSection,
    AssemblerInputs,
    ContextAssembler,
    PlannerAssembler,
    ReplyerAssembler,
    SectionStability,
    Snapshot,
)
from src.modules.context.snapshot import EnvironmentBlock, TimelineBlock, WorkingMemoryTrace

__all__ = [
    # 旧 ContextService（与新 ContextAssembler 并行共存，不删）
    "ContextService",
    "ContextServiceConfig",
    "ConversationMessage",
    "MessageRole",
    "SessionInfo",
    "StorageType",
    # Wave 3 新增
    "AssembledSection",
    "AssemblerInputs",
    "ContextAssembler",
    "PlannerAssembler",
    "ReplyerAssembler",
    "Snapshot",
    "SectionStability",
    "EnvironmentBlock",
    "TimelineBlock",
    "WorkingMemoryTrace",
]
