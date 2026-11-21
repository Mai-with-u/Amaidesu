"""
事件 Payload 定义：agents 域 / 控制面事实

- ``agent.prompted``：递话受理事件（运营或跨 Agent 递话成功送达目标时发一条）。
  递话不进任务账本（记账分家），本事件只做观测——前端时间线递话行与
  Agent 页"最近递话"的数据源。
- ``agent.replied``：命令驱动型 Agent 的每步响应事实（中间工具调用步骤发一条，
  自然终止轮的正文走 game.report 交付卡）——前端时间线响应卡的数据源，
  llm_request_id 供懒取缓存/Token/上下文统计。
- ``agent.lead.changed``：动态主从切换事实（主播与自主游戏 Agent 之间交权/收权时发一条）
  ——观察面与复盘记录的数据源。
- ``agent.lead.commentary``：游戏侧实况解说提交事实（提交即发一条，受理成功与每种失败都算事实）
  ——直播控制台解说卡的数据源。
- ``agent.lead.suggestion``：非强杀建议转达事实（转达即发一条，含拒收）——直播控制台建议卡的数据源。
"""

from typing import Literal, Optional

from pydantic import Field

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.utils.time_utils import now_ms


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


@register_event(CoreEvents.AGENT_LEAD_CHANGED)
class AgentLeadChangedPayload(BasePayload):
    """
    动态主从切换事实 Payload（由 LeadCoordinator 在主控权变化时发布）。

    Attributes:
        lead_agent: 切换后的主控 Agent 注册名（"streamer" 或具体游戏 Agent 注册名）
        previous_lead: 切换前的主控 Agent 注册名
        mode: 切换后的主控模式（"streamer"=主播主控 / "game"=游戏自主主控）
        reason: 本次主从切换原因说明
        manual_override: 当前人工覆盖策略（None / "auto" / "streamer" / 具体游戏名）
        handover_summary: 交权简报或收权收尾摘要
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    lead_agent: str = Field(..., description="切换后的主控 Agent 注册名（'streamer' 或具体游戏 Agent 名）")
    previous_lead: str = Field(..., description="切换前的主控 Agent 注册名")
    mode: Literal["streamer", "game"] = Field(..., description="切换后的主控模式（streamer / game）")
    reason: str = Field(default="", description="本次主从切换原因")
    manual_override: Optional[str] = Field(default=None, description="当前人工覆盖策略（auto / streamer / 游戏名）")
    handover_summary: str = Field(default="", description="交接或收尾摘要")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event(CoreEvents.AGENT_LEAD_COMMENTARY)
class AgentLeadCommentaryPayload(BasePayload):
    """
    游戏侧实况解说提交事实 Payload（由 LeadCoordinator 在受理解说提交时发布）。

    语义：游戏 Agent 提交了一条实况解说并得到受理结果——成功与每种失败都算事实。
    发布者：LeadCoordinator（``submit_commentary``，每条退出路径都发）。
    订阅者：直播控制台（解说卡：内容、情绪、受理状态）。

    Attributes:
        game: 提交方游戏 Agent 注册名
        summary: 解说正文摘要
        mood: 解说情绪基调（如 neutral / excited）
        urgency: 紧迫度（normal / high 等，供控制台排序标色）
        about_player: 解说涉及的游戏玩家标识（未涉及为空串）
        correction: 是否为对先前解说的更正
        accepted: 是否受理成功（转交主播发言管线）
        spoken: 受理后是否已实际成为主播发言（受理成功但未播出的中间态为 False）
        reason: 未受理时的失败码说明（not_in_lead / no_commentary_handler / handler_error 等）
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    game: str = Field(..., description="提交方游戏 Agent 注册名")
    summary: str = Field(..., description="解说正文摘要")
    mood: str = Field(default="neutral", description="解说情绪基调")
    urgency: str = Field(default="normal", description="紧迫度（normal / high 等）")
    about_player: str = Field(default="", description="解说涉及的游戏玩家标识；未涉及为空串")
    correction: bool = Field(default=False, description="是否为对先前解说的更正")
    accepted: bool = Field(..., description="是否受理成功（转交主播发言管线）")
    spoken: bool = Field(default=False, description="受理后是否已实际成为主播发言")
    reason: str = Field(default="", description="未受理时的失败码说明；受理成功为空串")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event(CoreEvents.AGENT_LEAD_SUGGESTION)
class AgentLeadSuggestionPayload(BasePayload):
    """
    非强杀建议转达事实 Payload（由 LeadCoordinator 在转达建议时发布）。

    语义：一条非强杀建议已转达（含拒收）。建议不打断在跑目标、不建委派账本。
    发布者：LeadCoordinator（``send_suggestion``，content 非空时每条路径都发）。
    订阅者：直播控制台（建议卡：内容、来源、受理状态）。

    Attributes:
        content: 建议内容全文
        source: 发起方标识（游戏 Agent 注册名 / operator=运营）
        target: 建议指向的目标 Agent 注册名（未指定为空串）
        lead_agent: 转达时的当前主控 Agent 注册名（未知为空串）
        accepted: 是否受理成功（已转达给主控方）
        reason: 拒收时的失败码说明；受理成功为空串
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    content: str = Field(..., description="建议内容全文")
    source: str = Field(default="streamer", description="发起方标识（游戏 Agent 注册名 / operator）")
    target: str = Field(default="", description="建议指向的目标 Agent 注册名；未指定为空串")
    lead_agent: str = Field(default="", description="转达时的当前主控 Agent 注册名；未知为空串")
    accepted: bool = Field(..., description="是否受理成功（已转达给主控方）")
    reason: str = Field(default="", description="拒收时的失败码说明；受理成功为空串")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = [
    "AgentLeadChangedPayload",
    "AgentLeadCommentaryPayload",
    "AgentLeadSuggestionPayload",
    "AgentPromptedPayload",
    "AgentRepliedPayload",
]
