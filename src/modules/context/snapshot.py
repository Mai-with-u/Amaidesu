"""
Snapshot 数据类（Wave 3 / §1.44）

ContextAssembler 组装后返回的稳定快照结构：
- 稳定 section（stable / cacheable）：常驻——system 人格 / 工具清单 / 环节描述 / 历史摘要
- 动态 section（per-call）：每次唤醒都变——直播间快照 / 工作记忆

JSON-序列化友好（dataclass slots=True）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class TimelineBlock:
    """时间块（分钟级；§1.44 时间块分钟级 + 放尾部）

    Attributes:
        start_ms: 块起始时刻（毫秒）
        end_ms: 块结束时刻（毫秒）
        summary: 该时间窗的摘要文本（已压缩）
        tags: 标签列表
    """

    start_ms: int
    end_ms: int
    summary: str
    tags: List[str] = field(default_factory=list)


@dataclass(slots=True)
class EnvironmentBlock:
    """直播间环境快照（§1.44 动态段）。"""

    minute_bucket_ms: int  # 分钟级时刻（避免秒级毒化缓存）
    duration_so_far_ms: int
    current_stage_label: Optional[str] = None  # 当前环节（agenda_runtime.current）
    unread_summary: str = ""  # 未读行为流摘要
    key_changes: List[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkingMemoryTrace:
    """工作记忆（本轮工具链的归一化历史，§1.51）。"""

    tool_call_pairs: List[Dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: List[str] = field(default_factory=list)


__all__ = ["TimelineBlock", "EnvironmentBlock", "WorkingMemoryTrace"]
