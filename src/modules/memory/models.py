"""Memory 模块数据类

- ``MemoryHit`` / ``MemoryWriteResult``：``MemoryProvider`` Protocol 的接口
  形态（当前无实现者——SimpleMemory 已转为观众事实/画像读写服务；Protocol
  作为"未来外部记忆后端再入口"的门保留，数据类随接口保留）
- ``ViewerFact``：一条"关于某观众的事实"（``viewer_facts`` 表行投影）
- ``ViewerProfile``：一份观众画像（``viewer_profiles`` 表行投影）
- ``ViewerProfileSummary``：画像候选行（画像增量生成的待处理清单条目）

## 命名准则
- 时间字段一律毫秒 int（``*_ms``）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(slots=True)
class MemoryHit:
    """单条召回命中（``MemoryProvider`` 接口形态，未来后端实现用）

    Attributes:
        memory_id: 记忆条目唯一 id
        kind: 记忆类型
        text: 文本内容
        score: 相关度分数（越大越相关）
        timestamp_ms: 写入时刻（毫秒 int）
        metadata: 额外元数据
    """

    memory_id: int
    kind: str
    text: str
    score: float
    timestamp_ms: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryWriteResult:
    """写入结果（``MemoryProvider`` 接口形态，未来后端实现用）

    Attributes:
        memory_id: 新写入条目 id（自增）
        accepted: 是否成功入库
        message: 备注（拒绝原因 / 信息）
    """

    memory_id: int
    accepted: bool
    message: str = ""


@dataclass(slots=True)
class ViewerFact:
    """一条观众事实（``viewer_facts`` 表一行的内存投影）

    Attributes:
        fact_id: 条目唯一 id（表主键）
        platform: 平台标识（身份键组成部分）
        user_id: 平台用户 ID（身份键组成部分）
        fact_text: 事实文本（必须由本人原话直接支持）
        source_message_id: 证据消息 ID（批内弹幕/SC 的 message_id，可溯源）
        created_at_ms: 提取落库时刻（毫秒 int）
    """

    fact_id: int
    platform: str
    user_id: str
    fact_text: str
    source_message_id: str
    created_at_ms: int


@dataclass(slots=True)
class ViewerProfile:
    """一份观众画像（``viewer_profiles`` 表一行的内存投影）

    Attributes:
        platform: 平台标识（身份键组成部分）
        user_id: 平台用户 ID（身份键组成部分）
        profile_text: 画像文本（LLM 增量压缩产物）
        last_compressed_at_ms: 增量压缩水位（该时刻前的原料已摄入画像）
        updated_at_ms: 最近更新时刻（毫秒 int）
    """

    platform: str
    user_id: str
    profile_text: str
    last_compressed_at_ms: int
    updated_at_ms: int


@dataclass(slots=True)
class ViewerProfileSummary:
    """画像候选行：水位后有新事实、且互动量达门槛的观众。

    画像增量生成的待处理清单条目——``need_rebuild`` 判定依据是
    最新事实时刻晚于画像水位（无画像行视为水位 0）。

    Attributes:
        platform: 平台标识
        user_id: 平台用户 ID
        interaction_count: viewers 表互动计数（门槛过滤字段）
    """

    platform: str
    user_id: str
    interaction_count: int


__all__ = [
    "MemoryHit",
    "MemoryWriteResult",
    "ViewerFact",
    "ViewerProfile",
    "ViewerProfileSummary",
]
