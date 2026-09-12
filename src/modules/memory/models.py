"""
MemoryProvider 数据类

- ``MemoryHit``：召回结果（一次命中）
- ``MemoryWriteResult``：写入结果

## 命名准则
- 写入时刻 / 重要度都用毫秒 int（``*_ms``）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(slots=True)
class MemoryHit:
    """单条召回命中

    Attributes:
        memory_id: 记忆条目唯一 id
        kind: 记忆类型（当前为 "fact"，即事实/事件记忆）
        text: 文本内容
        score: 相关度分数（越大越相关）
        timestamp_ms: 写入时刻（毫秒 int）
        metadata: 额外元数据（标签、来源等）
    """

    memory_id: int
    kind: str  # "fact"
    text: str
    score: float
    timestamp_ms: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryWriteResult:
    """写入结果

    Attributes:
        memory_id: 新写入条目 id（自增）
        accepted: 是否成功入库
        message: 备注（拒绝原因 / 信息）
    """

    memory_id: int
    accepted: bool
    message: str = ""


__all__ = ["MemoryHit", "MemoryWriteResult"]
