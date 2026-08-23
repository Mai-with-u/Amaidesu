"""
v2 语义域事件 Payload 定义：agenda.update

定义 ``agenda.update`` 事件 Payload（AgendaItem 运行进度变更）。
对应存储 ``agenda_runtime`` 表（见 .omo/drafts/amaidesu-v2-storage-schema.md）。

按契约（.omo/drafts/amaidesu-v2-event-contract.md "agenda.*" 节）：
- AgendaItem = **运行进度条目**（与 agenda_plan 原始大纲只读基准区分）
- action 判别："done"（打勾完成） / "schedule"（改时间表） / "insert"（插入新条目）
- ``inserted_by`` 字段区分人类编排者与 AI 自动插入（human/ai）
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


class AgendaItem(BaseModel):
    """
    AgendaItem — 运行进度条目（与存储 ``agenda_runtime`` 行一一对应）

    注意：AgendaItem **不是** ``agenda_plan``（原始大纲只读基准）。
    Plan 是 diff 的参考基准；Item 是运行期状态（可被 done/insert/schedule）。

    Attributes:
        plan_id: 关联到 ``agenda_plan.id``（原始计划基准）；insert 动作下可为空（独立条目）
        order: 在运行期列表中的排序（同一 plan 内单调递增）
        label: 人类可读条目名（如 "开场寒暄" / "游戏环节：MC 挖矿"）
        starts_at_ms: 计划开始时刻（Unix 毫秒，None 表示无固定时间）
        expected_ms: 预期持续时长（毫秒）；用于空转探测器判据
        note: 备注（人类填写 / AI 自动生成）
        done: 是否完成
        current: 是否当前激活（同一时刻最多一条 current=True）
        inserted_by: 插入者（human=人类编排 / ai=AI 自动插入）
    """

    plan_id: str = Field(default="", description="关联 agenda_plan.id；insert 动作下可为空字符串表示独立条目")
    order: int = Field(..., ge=0, description="运行期列表排序（单调递增）")
    label: str = Field(..., description="人类可读条目名")
    starts_at_ms: Optional[int] = Field(default=None, description="计划开始时刻（Unix 毫秒，None 表示无固定时间）")
    expected_ms: Optional[int] = Field(default=None, ge=0, description="预期持续时长（毫秒）")
    note: str = Field(default="", description="备注")
    done: bool = Field(default=False, description="是否完成")
    current: bool = Field(default=False, description="是否当前激活（同一时刻最多一条 current=True）")
    inserted_by: Literal["human", "ai"] = Field(default="ai", description="插入者")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "plan_id": "plan_20260822_main",
                "order": 3,
                "label": "游戏环节：MC 挖矿",
                "starts_at_ms": 1706745600000,
                "expected_ms": 1800000,
                "note": "需要找主播确认是否继续",
                "done": False,
                "current": True,
                "inserted_by": "human",
            }
        }
    )


@register_event("agenda.update")
class AgendaPayload(BasePayload):
    """
    AgendaItem 变更事件 Payload

    事件名：``agenda.update``
    发布者：Planner（调 ``update_agenda_item`` 工具后）→ 存储更新后发出
    订阅者：空转探测器、观察器（监控 AgendaItem 变化）

    Attributes:
        live_session_id: 场次 ID（与存储 agenda_runtime FK 一致）
        action: 变更类型（done=打勾 / schedule=改时间 / insert=插入新条目）
        item: 变更涉及的 AgendaItem
        changed_at_ms: 变更时刻（Unix 毫秒）
    """

    live_session_id: str = Field(..., description="场次唯一 ID")
    action: Literal["done", "schedule", "insert"] = Field(
        ...,
        description="变更类型。done=打勾完成 / schedule=改时间表 / insert=插入新条目",
    )
    item: AgendaItem = Field(..., description="变更涉及的 AgendaItem")
    changed_at_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="变更时刻（Unix 毫秒）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "live_session_id": "ls_20260822_001",
                "action": "done",
                "item": {
                    "plan_id": "plan_20260822_main",
                    "order": 1,
                    "label": "开场寒暄",
                    "starts_at_ms": 1706745600000,
                    "expected_ms": 300000,
                    "note": "",
                    "done": True,
                    "current": False,
                    "inserted_by": "human",
                },
                "changed_at_ms": 1706745900000,
            }
        }
    )


__all__ = [
    "AgendaItem",
    "AgendaPayload",
]
