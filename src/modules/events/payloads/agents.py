"""
事件 Payload 定义：agents 域 / 控制面事实

- ``agent.prompted``：递话受理事件（运营或跨 Agent 递话成功送达目标时发一条）。
  递话不进任务账本（记账分家），本事件只做观测——前端时间线递话行与
  Agent 页"最近递话"的数据源。
- ``agent.replied``：命令驱动型 Agent 的每步响应事实（中间工具调用步骤发一条，
  自然终止轮的正文走 game.report 交付卡）——前端时间线响应卡的数据源，
  llm_request_id 供懒取缓存/Token/上下文统计。
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


@register_event(CoreEvents.AGENT_REPLIED)
class AgentRepliedPayload(BasePayload):
    """
    Agent 每步响应事实 Payload（命令驱动型 Agent 的 ReAct 中间步骤，一步一条）。

    Attributes:
        agent: 发话 Agent 注册名（如 minecraft）
        content: 本步响应正文（LLM 的 content 通道输出）
        round_id: ReAct 轮次关联键（与思考流/工具卡同键；空串=无思考流注入）
        step: ReAct 步号（轮内从 1 递增）
        model: 本步请求实际使用的模型标识
        llm_request_id: 请求历史指针（观察面懒取缓存/Token/上下文统计的键）
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    agent: str = Field(..., description="发话 Agent 注册名")
    content: str = Field(default="", description="本步响应正文")
    round_id: str = Field(default="", description="ReAct 轮次关联键（空串=无思考流注入）")
    step: int = Field(default=0, description="ReAct 步号（轮内从 1 递增）")
    model: str = Field(default="", description="本步请求实际使用的模型标识")
    llm_request_id: str = Field(default="", description="请求历史指针（统计懒取键）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = ["AgentPromptedPayload", "AgentRepliedPayload"]
