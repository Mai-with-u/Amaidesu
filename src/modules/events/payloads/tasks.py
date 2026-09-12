"""task.* 事件 Payload（异步任务生命周期，ADR-013）

``task.changed``：任务记录表写入边界在状态**真的变化**时发布（同状态
幂等不重发）。消费者按 ``payload.initiator`` 过滤唤醒（BaseAgent 默认
行为）；事件只做通知/唤醒，任务事实源是记录表查询（通知是提示、查询
才是事实源）。
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event


@register_event("task.changed")
class TaskChangedPayload(BasePayload):
    """异步任务状态变化（task.changed）。

    Attributes:
        task_id: 任务号（回执型工具受理时分配；全链路关联键）
        status: 新状态（accepted / running / waiting_for_decision /
            succeeded / failed / cancelled / timeout）
        summary: 人可读摘要（变化要点或终态结论；截断防膨胀）
        initiator: 发起方 Agent 注册名（有变化通知谁——唤醒过滤键）
        executor: 执行者（provider 型 = 提供者名；agent 型 = 执行 Agent 名）
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）
        snapshot: 任务快照（执行侧自由 dict；嵌套任务在此带内层任务号）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    task_id: str = Field(..., description="任务号（受理回执分配，全链路关联键）")
    status: str = Field(..., description="新状态（词表见 tasks.py）")
    summary: str = Field(default="", description="变化摘要（人可读，截断防膨胀）")
    initiator: str = Field(..., description="发起方 Agent 注册名（唤醒过滤键）")
    executor: str = Field(default="", description="执行者（提供者名或执行 Agent 名）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    snapshot: Dict[str, Any] = Field(default_factory=dict, description="任务快照（执行侧自由 dict）")
    alert: bool = Field(
        default=False,
        description="停滞告警标记（True = 无进展提醒，status 保持当前值——唤醒类通知，非状态变化）",
    )
    timestamp_ms: int = Field(default=0, description="事件发布时间戳（Unix 毫秒）")


__all__ = ["TaskChangedPayload"]
