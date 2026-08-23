"""
v2 语义域事件 Payload 定义：planner.checkpoint

定义 ``planner.checkpoint`` 空转检查点事件 Payload（纯提醒零决策）。
对应 §1.7（Planner 空转探测机制）。

按契约（.omo/drafts/amaidesu-v2-event-contract.md "planner.checkpoint" 节）：
- 判据全过才发（Planner 空闲 + 无 pending 异步 + 事件队列空 + 有未完成 AgendaItem）
- 提供当前 AgendaItem 定位（``active``/``next``/``expected_ms``）让 Planner 知道"我在哪、要到哪去"
- ``timeline_summary`` 给出近期时序摘要（人类可读）
- ``duration_ms`` 标识本检查点的时间窗（毫秒）
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


class CheckpointAgendaPosition(BaseModel):
    """
    空转检查点 AgendaItem 定位（嵌套子结构）

    Attributes:
        active: 当前激活的 AgendaItem 标签（label）；空字符串表示无 current=True 条目
        next: 下一个 AgendaItem 标签；空字符串表示无后续
        expected_ms: 下一个 AgendaItem 的预期持续时长（毫秒），用于 Planner 决策参考
    """

    active: str = Field(default="", description="当前激活的 AgendaItem 标签（label）")
    next: str = Field(default="", description="下一个 AgendaItem 标签")
    expected_ms: Optional[int] = Field(default=None, ge=0, description="下一个 AgendaItem 预期持续时长（毫秒）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "active": "游戏环节：MC 挖矿",
                "next": "中场休息",
                "expected_ms": 600000,
            }
        }
    )


@register_event("planner.checkpoint")
class CheckpointPayload(BasePayload):
    """
    空转检查点事件 Payload（纯提醒零决策）

    事件名：``planner.checkpoint``
    发布者：空转探测器（后台轻循环，判据全过才发）
    订阅者：Planner（安静时问一句）、观察器

    Attributes:
        timestamp_ms: 检查点发布时间（Unix 毫秒）
        agenda_item: AgendaItem 定位（active / next / expected_ms）
        timeline_summary: 时序摘要（人类可读）
        duration_ms: 本检查点时间窗（毫秒）；观察器可据此做节流
    """

    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="检查点发布时间（Unix 毫秒）",
    )
    agenda_item: CheckpointAgendaPosition = Field(
        ...,
        description="AgendaItem 定位（active/next/expected_ms 三元组）",
    )
    timeline_summary: str = Field(
        default="",
        description="时序摘要（人类可读，简述近期发生的重要事件）",
    )
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="本检查点时间窗（毫秒）；观察器可据此做节流",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "timestamp_ms": 1706745600000,
                "agenda_item": {
                    "active": "游戏环节：MC 挖矿",
                    "next": "中场休息",
                    "expected_ms": 600000,
                },
                "timeline_summary": "过去 5 分钟：进入 MC 挖矿环节，挖到 3 个铁矿、1 个钻石",
                "duration_ms": 300000,
            }
        }
    )


__all__ = [
    "CheckpointAgendaPosition",
    "CheckpointPayload",
]
