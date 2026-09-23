"""
MemoryProvider 数据类

- ``MemoryHit``：召回结果（一次命中）
- ``MemoryWriteResult``：写入结果
- ``MemoryFact``：记忆条目全量行（管理面列表 / 增删改查用）
- ``MemoryStats``：记忆库总量统计（管理面概览用）

## 命名准则
- 写入时刻 / 重要度都用毫秒 int（``*_ms``）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


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


@dataclass(slots=True)
class MemoryFact:
    """记忆条目全量行（``_memory_facts`` 表一行的内存投影）

    Attributes:
        memory_id: 条目唯一 id（表主键）
        text: 文本内容
        source: 写入来源（如 agent 工具名 / "webui"）
        tags: 逗号连接的标签串（存储态；展示层自行拆分）
        importance: 重要度（召回排序权重，越大越靠前）
        timestamp_ms: 写入时刻（毫秒 int）
    """

    memory_id: int
    text: str
    source: str
    tags: str
    importance: int
    timestamp_ms: int


@dataclass(slots=True)
class MemoryStats:
    """记忆库总量统计（管理面概览）

    Attributes:
        total_facts: 条目总数
        sources: 各来源条目计数（按计数降序，``[(source, count), ...]``）
        latest_ms: 最新一条写入时刻（空库为 0）
    """

    total_facts: int
    sources: List[Tuple[str, int]] = field(default_factory=list)
    latest_ms: int = 0


__all__ = ["MemoryHit", "MemoryWriteResult", "MemoryFact", "MemoryStats"]
