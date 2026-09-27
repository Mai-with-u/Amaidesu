"""
事件 Payload 定义：agents 域 / 控制面事实

- ``agent.prompted``：递话受理事件（运营或跨 Agent 递话成功送达目标时发一条）。
  递话不进任务账本（记账分家），本事件只做观测——前端时间线递话行与
  Agent 页"最近递话"的数据源。
"""

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


@register_event(CoreEvents.AGENT_PROMPTED)
class AgentPromptedPayload(BasePayload):
    """
    递话受理事件 Payload（两条调用面同点收口：运营 REST / framework_prompt 原语）。

    Attributes:
        target: 目标 Agent 注册名（递话送达对象）
        content: 递话内容全文
        source: 发起方标识（operator=运营 / Agent 注册名=跨 Agent 递话）
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    target: str = Field(..., description="目标 Agent 注册名（递话送达对象）")
    content: str = Field(default="", description="递话内容全文")
    source: str = Field(default="", description="发起方标识（operator / 发起 Agent 注册名）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = ["AgentPromptedPayload"]
