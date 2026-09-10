"""
事件 Payload 定义：planner 域 / streamer 决策管线

- ``planner.decision``：决策轮记录事件（每轮两阶段决策结束发一条）
- ``streamer.stage``：决策管线阶段状态事件（状态变化即发射）
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event
from src.modules.time_utils import now_ms


class PlannerBatchItem(BaseModel):
    """
    决策轮弹幕批次条目（嵌套子结构，观察器展示"这轮决策看到了什么"）

    Attributes:
        message_id: 弹幕消息 ID（与 live_chat.message_id / reply_to_message_id 同键空间）
        user_id: 观众 user_id
        user_name: 观众昵称
        text: 弹幕文本（截断后的展示文本）
    """

    message_id: str = Field(default="", description="弹幕消息 ID")
    user_id: str = Field(default="", description="观众 user_id")
    user_name: str = Field(default="", description="观众昵称")
    text: str = Field(default="", description="弹幕文本（截断展示）")


@register_event("planner.decision")
class PlannerDecisionPayload(BasePayload):
    """
    决策轮记录事件 Payload（每轮两阶段决策结束发一条，成功/失败/降级全覆盖）

    事件名：``planner.decision``
    发布者：StreamerAgent（``_make_two_stage_decision`` 收口处）
    订阅者：观察器（决策卡渲染）、事件历史（回看）、互动分析（reply_to_message_id）

    该事件是"主播为什么这么做"的可观测事实：触发原因、决策结论（含低置信度
    被裁决压制的标记）、失败原因（LLM/解析/工具）、产出（发言/情绪/utterance）、
    耗时与 LLM 请求历史指针全部在这条事件里，观察者无需翻日志。

    Attributes:
        round_id: 决策轮次 ID（格式 ``rnd_{epoch_ms}_{seq}``，进程内单调递增），
            本轮弹幕批次、决策记录、发言、工具结果的共同关联键。
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）。
        trigger_reason: 触发原因（缓冲聚合原因 / proactive:* / dashboard:debug_test 等）
        proactive: 是否主动发言轮（无弹幕批次）
        forced: 是否强制回应轮（SC/礼物/上舰）
        batch: 本轮弹幕批次摘要（主动发言轮为空列表）
        should_reply: 决策结论：是否发言
        target: 决策面向的对象（用户名 / "all"）
        topic_summary: 话题摘要
        reply_guidance: 给 Replyer 的方向性指引
        confidence: 决策置信度 [0.0, 1.0]
        reply_to_message_id: 决策所回复弹幕的 message_id（Planner 输出；互动分析关联键）
        silent_reason: 静默原因标记。``low_confidence``=LLM 想回但被低置信度裁决
            压制（与"LLM 自己决定沉默"区分开）；None=无压制。
        speech: 主播发言文本（成功且有发言时）
        emotion: 关联情绪标签（可选）
        utterance_id: 发言实例 ID（成功且有发言时）
        error: 失败原因（planner_failed / reply_tool_failed 及细节；成功为 None）
        planner_raw: Planner LLM 原始返回文本（截断展示；完整内容经 llm_request_id 查请求历史）
        llm_request_id: 本轮 Planner LLM 调用的请求历史 ID（跳转完整请求的指针）
        planner_duration_ms: Planner 阶段耗时（毫秒）
        reply_duration_ms: Replyer 阶段耗时（毫秒；未进入回复阶段为 0）
        total_duration_ms: 本轮总耗时（毫秒）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    round_id: str = Field(..., description="决策轮次 ID（rnd_{epoch_ms}_{seq}）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    trigger_reason: str = Field(default="", description="触发原因")
    proactive: bool = Field(default=False, description="是否主动发言轮")
    forced: bool = Field(default=False, description="是否强制回应轮")
    batch: List[PlannerBatchItem] = Field(default_factory=list, description="本轮弹幕批次摘要")
    should_reply: bool = Field(default=False, description="决策结论：是否发言")
    target: Optional[str] = Field(default=None, description="决策面向的对象")
    topic_summary: str = Field(default="", description="话题摘要")
    reply_guidance: str = Field(default="", description="给 Replyer 的方向性指引")
    confidence: float = Field(default=0.0, description="决策置信度 [0.0, 1.0]")
    reply_to_message_id: Optional[str] = Field(
        default=None,
        description="决策所回复弹幕的 message_id（互动分析关联键）",
    )
    silent_reason: Optional[str] = Field(
        default=None,
        description="静默原因标记：low_confidence=低置信度裁决压制；None=无压制",
    )
    speech: Optional[str] = Field(default=None, description="主播发言文本（成功且有发言时）")
    emotion: Optional[str] = Field(default=None, description="关联情绪标签（可选）")
    utterance_id: Optional[str] = Field(default=None, description="发言实例 ID（成功且有发言时）")
    error: Optional[str] = Field(default=None, description="失败原因；成功为 None")
    planner_raw: str = Field(default="", description="Planner LLM 原始返回文本（截断展示）")
    llm_request_id: Optional[str] = Field(
        default=None,
        description="本轮 Planner LLM 调用的请求历史 ID（完整请求指针）",
    )
    planner_duration_ms: int = Field(default=0, ge=0, description="Planner 阶段耗时（毫秒）")
    reply_duration_ms: int = Field(default=0, ge=0, description="Replyer 阶段耗时（毫秒）")
    total_duration_ms: int = Field(default=0, ge=0, description="本轮总耗时（毫秒）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event("streamer.stage")
class StreamerStagePayload(BasePayload):
    """
    决策管线阶段状态事件 Payload（状态变化即发射，观察器渲染状态条）

    事件名：``streamer.stage``
    发布者：StreamerAgent（决策循环边界处）
    订阅者：观察器（"主播正在做什么/卡在哪"一眼可见）

    Attributes:
        stage: 阶段名（planning=决策中 / replying=生成中 / idle=空闲等待）
        agent_state: agent_state（running=执行中 / wait=等待输入）
        round_id: 关联决策轮次 ID（idle 阶段为刚结束的轮次，可能为 None）
        detail: 人类可读补充（如触发原因、耗时备注）
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    stage: str = Field(..., description="阶段名：planning / replying / idle")
    agent_state: str = Field(default="running", description="agent_state：running / wait")
    round_id: Optional[str] = Field(default=None, description="关联决策轮次 ID")
    detail: str = Field(default="", description="人类可读补充说明")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


@register_event("planner.verdict")
class PlannerVerdictPayload(BasePayload):
    """
    裁决事件：Planner 决定回应并触发 reply 工具的时刻发出（表达生成之前）。

    事件名：``planner.verdict``
    发布者：ReplyToolProvider（reply 工具 invoke 入口处）
    订阅者：观察器（裁决卡实时渲染）

    与 ``planner.decision`` 的分工：
    - verdict = 裁决发生时刻的即时事实（回应意图 + 置信度），观察者实时可见，
      出现于查证工具卡之后、表达生成之前
    - decision = 轮末统计汇总（耗时/原始输出/请求指针），观察器按 round_id
      回填到裁决卡，不渲染重复卡片；沉默轮无 verdict，decision 独立成卡

    Attributes:
        round_id: 决策轮次 ID（与 decision/发言/工具结果共用关联键）
        live_session_id: 场次主键（发布方不填，由场次盖章拦截器注入；0=未归属）
        topic_summary: 话题摘要（Planner 的 reply 调用参数）
        reply_guidance: 给 Replyer 的方向性指引
        confidence: 决策置信度 [0.0, 1.0]
        target: 决策面向的对象
        reply_to_message_id: 决策所回复弹幕的 message_id
        timestamp_ms: 事件发布时间戳（Unix 毫秒）
    """

    round_id: str = Field(..., description="决策轮次 ID（rnd_{epoch_ms}_{seq}）")
    live_session_id: int = Field(
        default=0,
        description="场次主键（live_sessions.id）；发布方不填，由场次盖章拦截器注入；0=未归属",
    )
    topic_summary: str = Field(default="", description="话题摘要")
    reply_guidance: str = Field(default="", description="给 Replyer 的方向性指引")
    confidence: float = Field(default=0.0, description="决策置信度 [0.0, 1.0]")
    target: Optional[str] = Field(default=None, description="决策面向的对象")
    reply_to_message_id: Optional[str] = Field(default=None, description="决策所回复弹幕的 message_id")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="事件发布时间戳（Unix 毫秒）",
    )


__all__ = [
    "PlannerBatchItem",
    "PlannerDecisionPayload",
    "PlannerVerdictPayload",
    "StreamerStagePayload",
]
