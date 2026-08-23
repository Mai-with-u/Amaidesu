"""
MemoryProvider 数据类（Wave 3 / §1.50）

- ``MemoryHit``：召回结果（一次命中）
- ``MemoryWriteResult``：写入结果
- ``PersonProfile``：观众语义画像（与 viewers 表的客观统计正交）

## 命名准则（§1.6 / §1.50）
- 观众画像字段用语义词（标签 / 摘要），**不是**客观统计（统计归 viewers 表）
- Profile 键 = ``user_id = person_id``（与 viewers 主键同键）
- 写入时刻 / 重要度都用毫秒 int（``*_ms``）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class MemoryHit:
    """单条召回命中

    Attributes:
        memory_id: 记忆条目唯一 id
        kind: "fact"（事件/事实）/ "profile"（观众画像）
        text: 文本内容
        score: 相关度分数（越大越相关）
        timestamp_ms: 写入时刻（毫秒 int）
        metadata: 额外元数据（标签、来源等）
    """

    memory_id: int
    kind: str  # "fact" | "profile"
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
class PersonProfile:
    """观众语义画像

    ## 设计要点（§1.50 简单版）
    - 主键 = ``person_id``（= viewers.user_id）
    - 标签列表 + 摘要文本（LLM 总结"是怎样的人"）
    - **注意**：观众客观统计（message_count / gift_count 等）归 viewers 表；
      本类只承载**语义画像**，两者**正交不重叠**
    - 命名准则：画像用语义词（标签/摘要），不用感情词（intimacy/亲密度）

    Attributes:
        person_id: 观众 id（跨场持久化）
        display_name: 当前昵称（快照）
        tags: 标签列表（语义词，如 "爱提技术问题"、"老观众"）
        summary: 自由文本摘要
        updated_at_ms: 最近更新时刻（毫秒）
    """

    person_id: str
    display_name: str = ""
    tags: List[str] = field(default_factory=list)
    summary: str = ""
    updated_at_ms: int = 0


__all__ = ["MemoryHit", "MemoryWriteResult", "PersonProfile"]
